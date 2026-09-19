"""``metapool`` (update / delete): registration, discovery, CAS and execution."""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.base import ActionOperation, StalePolicy
from uds.mutability.types.meta_pools import MetaPoolUpdate
from uds.REST.methods.meta_pools import MetaPools

from tests.fixtures.authenticators import create_db_authenticator, create_db_groups
from tests.fixtures.services import (
    create_db_metapool,
    create_db_osmanager,
    create_db_provider,
    create_db_service,
    create_db_servicepool,
)
from tests.mcp.mutability._helpers import FlowTestCase, make_request


def _create_metapool() -> models.MetaPool:
    authenticator = create_db_authenticator()
    _groups = create_db_groups(authenticator, 1)
    service = create_db_service(create_db_provider())
    pool = create_db_servicepool(service=service, osmanager=create_db_osmanager())
    return create_db_metapool([pool], _groups)


def _bound(operation: str) -> MetaPoolUpdate:
    factory = registry_get(f"metapool.{operation}")
    assert factory is not None
    return typing.cast(MetaPoolUpdate, factory())


class MetaPoolUpdateRegistryTest(FlowTestCase):
    def test_both_operations_are_registered(self) -> None:
        found = registry_get("metapool.update")
        assert found is not None
        self.assertIs(type(found()), MetaPoolUpdate)
        self.assertIs(type(_bound("delete")), MetaPoolUpdate)
        self.assertIn("metapool.update", all_type_ids())
        self.assertIn("metapool.delete", all_type_ids())
        self.assertEqual(
            MetaPoolUpdate.supported_operations(),
            frozenset({ActionOperation.UPDATE, ActionOperation.DELETE}),
        )

    def test_stale_policy_is_deny_for_update_and_delete(self) -> None:
        # The meta pool DELETE removes the row synchronously, so both
        # operations ride the strict whole-item CAS (no FORCE overrides)
        self.assertIs(_bound("update").get_stale_policy(), StalePolicy.DENY)
        self.assertIs(_bound("delete").get_stale_policy(), StalePolicy.DENY)

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            MetaPoolUpdate().resolve_target("00000000-0000-0000-0000-000000000000")


class MetaPoolUpdateFieldsTest(FlowTestCase):
    def test_mutable_fields_carry_policy_choices(self) -> None:
        defs = MetaPoolUpdate().field_definitions("metapool")
        by_name = {d["name"]: d for d in defs}

        for name in (
            "name",
            "short_name",
            "comments",
            "tags",
            "visible",
            "policy",
            "ha_policy",
            "transport_grouping",
            "calendar_message",
        ):
            self.assertIn(name, by_name)
        # References and relations are not proposable
        for name in ("image_id", "servicesPoolGroup_id", "members"):
            self.assertNotIn(name, by_name)

        # Choices come straight from the gui and carry the human labels the
        # admin sees (option A of the gui coupling: no re-typed enum ids)
        self.assertEqual([c["value"] for c in by_name["policy"]["choices"]], [0, 1, 2])
        self.assertEqual([c["value"] for c in by_name["ha_policy"]["choices"]], [0, 1])
        self.assertEqual(
            [c["value"] for c in by_name["transport_grouping"]["choices"]],
            [0, 1, 2],
        )
        self.assertTrue(all(c["label"] for c in by_name["policy"]["choices"]))

        # Labels and tooltips match the gui (the source of truth), not the
        # previously hardcoded text
        self.assertEqual(by_name["policy"]["label"], "Load balancing policy")
        self.assertEqual(by_name["transport_grouping"]["label"], "Transport Selection")

        # GUI-provided constraints reach the agent surface
        self.assertEqual(by_name["short_name"]["length"], 32)
        self.assertTrue(by_name["name"]["required"])

        errors = MetaPoolUpdate().validate_values("metapool", {"visible": "yes"})
        self.assertTrue(any("must be a boolean" in e for e in errors))
        errors = MetaPoolUpdate().validate_values("metapool", {"members": []})
        self.assertTrue(any("unknown fields" in e for e in errors))

    def test_delete_publishes_no_fields(self) -> None:
        self.assertEqual(_bound("delete").field_definitions("metapool"), [])
        self.assertEqual(_bound("delete").validate_values("metapool", {}, None), [])
        self.assertIn("Propose deleting a meta pool", _bound("delete").tool_title())
        self.assertIn("queued until an administrator approves", _bound("delete").tool_description())

    def test_snapshot_covers_columns_and_context(self) -> None:
        authenticator = create_db_authenticator()
        _groups = create_db_groups(authenticator, 1)
        service = create_db_service(create_db_provider())
        pool = create_db_servicepool(service=service, osmanager=create_db_osmanager())
        meta_pool = create_db_metapool([pool], _groups)
        action_type = MetaPoolUpdate()

        snapshot = action_type.snapshot_values(meta_pool, action_type.etag_fields("metapool"))
        self.assertEqual(snapshot["image_id"], "-1")
        self.assertEqual(snapshot["servicesPoolGroup_id"], "-1")
        self.assertEqual(snapshot["policy"], meta_pool.policy)
        self.assertEqual(snapshot["ha_policy"], meta_pool.ha_policy)
        self.assertEqual(snapshot["transport_grouping"], meta_pool.transport_grouping)

        base = action_type.fingerprint(meta_pool)
        self.assertEqual(base, action_type.fingerprint(meta_pool))
        meta_pool.policy = 1
        meta_pool.save(update_fields=["policy"])
        self.assertNotEqual(base, action_type.fingerprint(meta_pool))


class MetaPoolUpdateExecuteTest(FlowTestCase):
    def test_execute_sends_whole_form_with_proposed_values(self) -> None:
        meta_pool = _create_metapool()
        action = self._action(
            self._flow(),
            action_type="metapool.update",
            target_uuid=meta_pool.uuid,
            values={"policy": 1},
            base_values={"policy": meta_pool.policy},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types.meta_pools.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            summary = async_to_sync(_bound("update").execute)(action, request=make_request())

        self.assertIn("updated", summary)
        execute_call = proxy_cls.return_value.execute.call_args
        self.assertIs(execute_call[0][0].handler, MetaPools)
        params: dict[str, typing.Any] = execute_call[0][2]
        # The PUT is form-shaped: every required field travels, proposed
        # values win, context keeps stored values
        for name in (
            "name",
            "short_name",
            "comments",
            "tags",
            "image_id",
            "servicesPoolGroup_id",
            "visible",
            "policy",
            "ha_policy",
            "calendar_message",
            "transport_grouping",
        ):
            self.assertIn(name, params)
        self.assertEqual(params["policy"], 1)
        self.assertEqual(params["image_id"], "-1")
        self.assertEqual(params["servicesPoolGroup_id"], "-1")

    def test_delete_removes_the_meta_pool_row(self) -> None:
        meta_pool = _create_metapool()
        action = self._action(
            self._flow(),
            action_type="metapool.delete",
            target_uuid=meta_pool.uuid,
            values={},
            base_values={},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types.meta_pools.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="OK")
            summary = async_to_sync(_bound("delete").execute)(action, request=make_request())

        self.assertIn("deleted", summary)
        execute_call = proxy_cls.return_value.execute.call_args
        target = execute_call[0][0]
        self.assertIs(target.handler, MetaPools)
        self.assertEqual(target.path, "meta_pools")
        self.assertEqual(target.method, types.rest.CustomMethodMethod.DELETE)
        self.assertEqual(target.args, (meta_pool.uuid,))
        self.assertEqual(target.parent, None)
        self.assertEqual(execute_call[1], {})

    def test_delete_aborts_when_the_meta_pool_vanished(self) -> None:
        meta_pool = _create_metapool()
        action = self._action(
            self._flow(),
            action_type="metapool.delete",
            target_uuid=meta_pool.uuid,
            values={},
            base_values={},
            base_etag="etag",
        )
        meta_pool.delete()
        with self.assertRaises(rest_exceptions.NotFound):
            async_to_sync(_bound("delete").execute)(action, request=make_request())
