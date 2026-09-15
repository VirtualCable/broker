"""``metapool.members.set``: relation action type (option A, full desired set)."""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.types.meta_pools import MetaPoolMembers
from uds.REST.methods.meta_pools import MetaPools
from uds.REST.methods.meta_service_pools import MetaServicesPool

from tests.fixtures.authenticators import create_db_authenticator, create_db_groups
from tests.fixtures.services import (
    create_db_metapool,
    create_db_osmanager,
    create_db_provider,
    create_db_service,
    create_db_servicepool,
)
from tests.mcp.mutability._helpers import FlowTestCase, make_request


def _pool() -> models.ServicePool:
    service = create_db_service(create_db_provider())
    return create_db_servicepool(service=service, osmanager=create_db_osmanager())


def _metapool(pools: list[models.ServicePool]) -> models.MetaPool:
    authenticator = create_db_authenticator()
    groups = create_db_groups(authenticator, 1)
    return create_db_metapool(pools, groups)


def _members() -> typing.Any:
    """SET binding of the members family (registry factories bind the operation)."""
    factory = registry_get("metapool.members.set")
    assert factory is not None
    return factory()


class MetaPoolMembersRegistryTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("metapool.members.set")
        assert found is not None
        self.assertIs(type(found()), MetaPoolMembers)
        self.assertIn("metapool.members.set", all_type_ids())

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            _members().resolve_target("00000000-0000-0000-0000-000000000000")

    def test_is_target_scoped(self) -> None:
        self.assertIs(MetaPoolMembers.target_scoped_fields, True)


class MetaPoolMembersFieldsTest(FlowTestCase):
    def test_single_editlist_field_with_pool_choices(self) -> None:
        pool_a = _pool()
        pool_b = _pool()
        defs = _members().field_definitions("metapool")
        self.assertEqual([d["name"] for d in defs], ["members"])
        members_def = defs[0]
        self.assertEqual(members_def["type"], types.ui.FieldType.EDITABLELIST.value)
        choices = {c["value"]: c["label"] for c in members_def["choices"]}
        self.assertEqual(choices[pool_a.uuid], pool_a.name)
        self.assertEqual(choices[pool_b.uuid], pool_b.name)

    def test_valid_complete_set(self) -> None:
        meta_pool = _metapool([_pool(), _pool()])
        rows = [
            {"pool_id": m.pool.uuid, "priority": m.priority, "enabled": m.enabled}
            for m in meta_pool.members.all()
        ]
        self.assertEqual(_members().validate_values("metapool", {"members": rows}, meta_pool), [])
        # Empty set is valid: it means "remove all members"
        self.assertEqual(_members().validate_values("metapool", {"members": []}, meta_pool), [])

    def test_missing_members_field_rejected(self) -> None:
        errors = _members().validate_values("metapool", {"other": 1})
        self.assertTrue(any("unknown fields" in e for e in errors))
        self.assertTrue(any("members is required" in e for e in errors))

    def test_row_shape_errors(self) -> None:
        pool = _pool()
        base = {"pool_id": pool.uuid, "priority": 0, "enabled": True}
        bad_rows: list[tuple[dict[str, typing.Any], str]] = [
            ({**base, "extra": 1}, "unknown keys"),
            ({"pool_id": pool.uuid, "priority": 0}, "missing keys"),
            ({**base, "pool_id": "no-uuid"}, "not a valid uuid"),
            (
                {**base, "pool_id": "00000000-0000-0000-0000-000000000000"},
                "does not match any existing service pool",
            ),
            ({**base, "priority": "high"}, "priority must be an integer"),
            ({**base, "priority": True}, "priority must be an integer"),
            ({**base, "priority": -1}, "priority must be >= 0"),
            ({**base, "enabled": "yes"}, "enabled must be a boolean"),
        ]
        for row, fragment in bad_rows:
            errors = _members().validate_values("metapool", {"members": [row]})
            self.assertTrue(any(fragment in e for e in errors), f"{row!r} -> {errors}")

    def test_duplicate_pool_id_rejected(self) -> None:
        pool = _pool()
        row = {"pool_id": pool.uuid, "priority": 0, "enabled": True}
        errors = _members().validate_values("metapool", {"members": [row, dict(row)]})
        self.assertTrue(any("duplicates pool_id" in e for e in errors))

    def test_case_insensitive_uuid_is_normalized(self) -> None:
        pool = _pool()
        errors = _members().validate_values(
            "metapool", {"members": [{"pool_id": pool.uuid.upper(), "priority": 0, "enabled": True}]}
        )
        self.assertEqual(errors, [])

    def test_live_duplicated_rows_rejected(self) -> None:
        pool = _pool()
        meta_pool = _metapool([pool])
        models.MetaPoolMember.objects.create(pool=pool, meta_pool=meta_pool, priority=7, enabled=False)
        rows = [{"pool_id": pool.uuid, "priority": 0, "enabled": True}]
        errors = _members().validate_values("metapool", {"members": rows}, meta_pool)
        self.assertTrue(any("duplicated member rows" in e for e in errors))


class MetaPoolMembersCasTest(FlowTestCase):
    def test_snapshot_canonical_rows(self) -> None:
        pool_a = _pool()
        pool_b = _pool()
        meta_pool = _metapool([pool_a, pool_b])
        action_type = _members()
        snapshot = action_type.snapshot_values(meta_pool, ["members"])
        expected = sorted(
            [
                {"pool_id": pool_a.uuid, "priority": 0, "enabled": True},
                {"pool_id": pool_b.uuid, "priority": 1, "enabled": True},
            ],
            key=lambda row: typing.cast(str, row["pool_id"]),
        )
        self.assertEqual(snapshot["members"], expected)

    def test_fingerprint_tracks_membership_only(self) -> None:
        pool = _pool()
        meta_pool = _metapool([pool])
        action_type = _members()

        base = action_type.fingerprint(meta_pool)
        self.assertEqual(base, action_type.fingerprint(meta_pool))

        # Membership changes move the fingerprint
        member = meta_pool.members.first()
        assert member is not None
        member.enabled = False
        member.save(update_fields=["enabled"])
        self.assertNotEqual(base, action_type.fingerprint(meta_pool))

    def test_diff_against_live_set(self) -> None:
        pool_a = _pool()
        pool_b = _pool()
        meta_pool = _metapool([pool_a, pool_b])
        action_type = _members()
        live = action_type.snapshot_values(meta_pool, ["members"])
        flow = self._flow()
        action = self._action(
            flow,
            action_type="metapool.members.set",
            target_uuid=meta_pool.uuid,
            values={"members": [live["members"][0]]},  # keep one, drop the other
            base_values=live,
            base_etag=action_type.fingerprint(meta_pool),
        )
        drifted = action_type.field_drift(
            action, ref_values={"members": [{"pool_id": "stale", "priority": 0, "enabled": True}]}
        )
        self.assertIn("members", drifted)
        self.assertNotIn("members", action_type.field_drift(action, ref_values={"members": live["members"]}))


class MetaPoolMembersExecuteTest(FlowTestCase):
    def _run(self, meta_pool: models.MetaPool, members: list[dict[str, typing.Any]]) -> mock.Mock:
        action = self._action(
            self._flow(),
            action_type="metapool.members.set",
            target_uuid=meta_pool.uuid,
            values={"members": members},
            base_values={},
            base_etag="etag",
        )
        proxy_cls = mock.MagicMock()
        proxy_cls.return_value.execute = mock.AsyncMock(return_value=None)
        with mock.patch("uds.mutability.types.meta_pools.RestProxy", proxy_cls):
            summary = async_to_sync(_members().execute)(action, request=make_request())
        self._last_summary = summary
        return proxy_cls

    @staticmethod
    def _rows(meta_pool: models.MetaPool) -> list[dict[str, typing.Any]]:
        return [
            {"pool_id": member.pool.uuid, "priority": member.priority, "enabled": member.enabled}
            for member in meta_pool.members.all()
        ]

    def test_diff_applies_delete_create_update_in_order(self) -> None:
        kept = _pool()
        dropped = _pool()
        added = _pool()
        meta_pool = _metapool([kept, dropped])
        rows = self._rows(meta_pool)
        kept_row = next(row for row in rows if row["pool_id"] == kept.uuid)
        kept_row["priority"] = 5  # update
        desired = [
            kept_row,
            {"pool_id": added.uuid, "priority": 0, "enabled": True},
        ]  # keep+update, add; drop the rest

        proxy_cls = self._run(meta_pool, desired)

        calls = proxy_cls.return_value.execute.await_args_list
        verbs = [call[0][0].method for call in calls]
        self.assertEqual(
            verbs,
            [
                types.rest.CustomMethodMethod.DELETE,  # dropped first
                types.rest.CustomMethodMethod.POST,  # then added
                types.rest.CustomMethodMethod.PUT,  # then modified
            ],
        )
        for call in calls:
            target = call[0][0]
            self.assertIs(target.handler, MetaServicesPool)
            self.assertEqual(target.path, "meta_pools/{uuid}/pools")
            self.assertIs(target.parent.handler, MetaPools)
            self.assertEqual(call[1]["parent_uuid"], meta_pool.uuid)

        delete_call = calls[0]
        dropped_member_uuid = models.MetaPoolMember.objects.get(pool=dropped, meta_pool=meta_pool).uuid
        self.assertEqual(delete_call[0][0].args, (dropped_member_uuid,))
        self.assertEqual(delete_call[0][2], {})

        create_call = calls[1]
        self.assertEqual(create_call[0][0].args, ())
        self.assertEqual(create_call[0][2], {"pool_id": added.uuid, "priority": 0, "enabled": True})

        update_call = calls[2]
        kept_member_uuid = models.MetaPoolMember.objects.get(pool=kept, meta_pool=meta_pool).uuid
        self.assertEqual(update_call[0][0].args, (kept_member_uuid,))
        self.assertEqual(update_call[0][2], {"pool_id": kept.uuid, "priority": 5, "enabled": True})

        self.assertIn("1 created, 1 modified, 1 removed", self._last_summary)

    def test_equal_set_is_a_noop(self) -> None:
        meta_pool = _metapool([_pool(), _pool()])
        proxy_cls = self._run(meta_pool, self._rows(meta_pool))
        proxy_cls.return_value.execute.assert_not_awaited()
        self.assertIn("0 created, 0 modified, 0 removed", self._last_summary)

    def test_empty_set_removes_everything(self) -> None:
        pool_a = _pool()
        pool_b = _pool()
        meta_pool = _metapool([pool_a, pool_b])
        proxy_cls = self._run(meta_pool, [])
        self.assertEqual(proxy_cls.return_value.execute.await_count, 2)
        for call in proxy_cls.return_value.execute.await_args_list:
            self.assertEqual(call[0][0].method, types.rest.CustomMethodMethod.DELETE)
        self.assertIn("2 removed", self._last_summary)

    def test_live_duplicated_rows_abort_before_touching_rest(self) -> None:
        pool = _pool()
        meta_pool = _metapool([pool])
        models.MetaPoolMember.objects.create(pool=pool, meta_pool=meta_pool, priority=7, enabled=False)
        with self.assertRaises(rest_exceptions.RequestError):
            self._run(meta_pool, [{"pool_id": pool.uuid, "priority": 0, "enabled": True}])
