"""``metapool.assignment``: update, custom and delete over meta assigned services.

Same shape as ``servicepool.assignment`` but every member is resolved
through the meta pool projection (the assigned services of the member
pools), the reset capability is per member service (not pool wide) and
the one-service-per-user rule is enforced per member pool service, as
the REST detail does.
"""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.base import ActionOperation, MutableActionType, StalePolicy
from uds.mutability.types.meta_pools import assignment as assignment_module
from uds.mutability.types.meta_pools.assignment import MetaPoolAssignment
from uds.REST.methods.meta_pools import MetaPools
from uds.REST.methods.meta_service_pools import MetaAssignedService

from tests.fixtures.authenticators import create_db_authenticator, create_db_groups
from tests.fixtures.services import (
    create_db_metapool,
    create_db_osmanager,
    create_db_provider,
    create_db_publication,
    create_db_service,
    create_db_servicepool,
    create_db_userservice,
)
from tests.fixtures.modules.service.service import TestServiceCache
from tests.mcp.mutability._helpers import FlowTestCase, make_request

State = types.states.State

RESET = assignment_module.RESET


def _metapool() -> models.MetaPool:
    authenticator = create_db_authenticator()
    groups = create_db_groups(authenticator, 1)
    service = create_db_service(create_db_provider())
    pool = create_db_servicepool(service=service, osmanager=create_db_osmanager())
    return create_db_metapool([pool], groups)


def _first_member_pool(meta_pool: models.MetaPool) -> models.ServicePool:
    member = meta_pool.members.select_related("pool").first()
    assert member is not None
    return member.pool


def _assigned(
    pool: models.ServicePool,
    user: models.User,
    state: str = State.USABLE,
) -> models.UserService:
    row = create_db_userservice(pool, create_db_publication(pool), user)
    if state != row.state:
        row.state = state
        row.save(update_fields=["state"])
    return row


def _action_type(operation: str) -> MutableActionType:
    factory = registry_get(f"metapool.assignment.{operation}")
    assert factory is not None
    return factory()


_reset_supported = mock.patch.object(TestServiceCache, "can_reset", True)


class MetaPoolAssignmentRegistryTest(FlowTestCase):
    def test_all_operations_are_registered(self) -> None:
        for operation in ("update", "custom", "delete"):
            self.assertIs(type(_action_type(operation)), MetaPoolAssignment)
            self.assertIn(f"metapool.assignment.{operation}", all_type_ids())
        self.assertNotIn("metapool.assignment.add", all_type_ids())
        self.assertEqual(
            MetaPoolAssignment.supported_operations(),
            frozenset({ActionOperation.UPDATE, ActionOperation.CUSTOM, ActionOperation.DELETE}),
        )

    def test_read_override_publishes_a_read(self) -> None:
        self.assertTrue(MetaPoolAssignment.readable())

    def test_stale_policy_is_force_for_async_operations_and_deny_for_update(self) -> None:
        self.assertIs(_action_type("custom").get_stale_policy(), StalePolicy.FORCE)
        self.assertIs(_action_type("delete").get_stale_policy(), StalePolicy.FORCE)
        self.assertIs(_action_type("update").get_stale_policy(), StalePolicy.DENY)

    def test_is_target_scoped(self) -> None:
        self.assertIs(MetaPoolAssignment.target_scoped_fields, True)

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            _action_type("custom").resolve_target("00000000-0000-0000-0000-000000000000")

    def test_read_view_publishes_projection_members_and_verbs(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        view = MetaPoolAssignment().read(meta_pool.uuid)
        self.assertEqual(view["entity_type"], "metapool.assignment")
        self.assertEqual(view["target_uuid"], meta_pool.uuid)
        self.assertEqual(
            view["member_pools"],
            [{"uuid": member_pool.uuid, "name": member_pool.name, "enabled": True}],
        )
        # TestServiceCache.can_reset is False: reset is not offered on the row
        by_uuid = {item["uuid"]: item for item in view["user_services"]}
        self.assertEqual(by_uuid[row.uuid]["state"], State.USABLE)
        self.assertEqual(by_uuid[row.uuid]["owner"], self.owner.pretty_name)
        self.assertEqual(by_uuid[row.uuid]["owner_uuid"], self.owner.uuid)
        self.assertEqual(by_uuid[row.uuid]["pool_id"], member_pool.uuid)
        self.assertEqual(by_uuid[row.uuid]["supported_actions"], [])


class MetaPoolAssignmentFieldsTest(FlowTestCase):
    def test_custom_publishes_action_and_user_service(self) -> None:
        defs = _action_type("custom").field_definitions("metapool")
        self.assertEqual([d["name"] for d in defs], ["action", "user_service"])
        self.assertEqual(defs[0]["type"], types.ui.FieldType.CHOICE.value)
        self.assertEqual(defs[0]["choices"], ["reset"])

    def test_delete_publishes_only_the_user_service_field(self) -> None:
        defs = _action_type("delete").field_definitions("metapool")
        self.assertEqual([d["name"] for d in defs], ["user_service"])

    def test_update_publishes_user_service_and_user(self) -> None:
        defs = _action_type("update").field_definitions("metapool")
        self.assertEqual([d["name"] for d in defs], ["user_service", "user"])
        self.assertIn("at most one user service of each member pool", defs[1]["tooltip"])
        self.assertIn("list_authenticators_users", defs[1]["tooltip"])

    def test_custom_choices_are_static_capability_is_per_row(self) -> None:
        # Unlike the service pool family, the CHOICE cannot be filtered by
        # a pool-wide capability: the member service behind the target row
        # decides, and validation refuses the doomed pairs.
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        defs = _action_type("custom").field_definitions("metapool", meta_pool)
        self.assertEqual(defs[0]["choices"], ["reset"])
        errors = _action_type("custom").validate_values(
            "metapool", {"action": "reset", "user_service": row.uuid}, meta_pool
        )
        self.assertTrue(any("does not support the 'reset' action" in e for e in errors))
        with _reset_supported:
            errors = _action_type("custom").validate_values(
                "metapool", {"action": "reset", "user_service": row.uuid}, meta_pool
            )
        self.assertEqual(errors, [])


class MetaPoolAssignmentCustomValidationTest(FlowTestCase):
    def test_action_field_is_required(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        errors = _action_type("custom").validate_values("metapool", {"user_service": row.uuid}, meta_pool)
        self.assertTrue(any("action is required" in e for e in errors))

    def test_unknown_action_rejected(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        errors = _action_type("custom").validate_values(
            "metapool", {"action": "reboot", "user_service": row.uuid}, meta_pool
        )
        self.assertTrue(any("'reboot' is not supported" in e for e in errors))

    def test_only_usable_services_can_be_reset(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        preparing = _assigned(member_pool, self.owner, state=State.PREPARING)
        with _reset_supported:
            errors = _action_type("custom").validate_values(
                "metapool", {"action": "reset", "user_service": preparing.uuid}, meta_pool
            )
        self.assertTrue(any("only usable services can be reset" in e for e in errors))

    def test_must_be_inside_the_projection(self) -> None:
        meta_pool = _metapool()
        foreign = _assigned(_first_member_pool(_metapool()), self.owner)
        with _reset_supported:
            errors = _action_type("custom").validate_values(
                "metapool", {"action": "reset", "user_service": foreign.uuid}, meta_pool
            )
        self.assertTrue(any("is not an assigned service of this meta pool" in e for e in errors))

    def test_user_service_field_is_required(self) -> None:
        meta_pool = _metapool()
        errors = _action_type("custom").validate_values("metapool", {"action": "reset"}, meta_pool)
        self.assertTrue(any("user_service is required" in e for e in errors))

    def test_malformed_uuid_rejected(self) -> None:
        meta_pool = _metapool()
        errors = _action_type("delete").validate_values("metapool", {"user_service": "not-a-uuid"}, meta_pool)
        self.assertTrue(any("not a valid uuid" in e for e in errors))
        errors = _action_type("delete").validate_values("metapool", {"user_service": 7}, meta_pool)
        self.assertTrue(any("must be the uuid string" in e for e in errors))


class MetaPoolAssignmentDeleteValidationTest(FlowTestCase):
    def test_release_accepts_usable_preparing_and_removing(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        for state in (State.USABLE, State.PREPARING, State.REMOVING):
            row = _assigned(member_pool, self.owner, state=state)
            self.assertEqual(
                _action_type("delete").validate_values("metapool", {"user_service": row.uuid}, meta_pool),
                [],
            )

    def test_release_rejects_removable(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner, state=State.REMOVABLE)
        errors = _action_type("delete").validate_values("metapool", {"user_service": row.uuid}, meta_pool)
        self.assertTrue(any("only usable, preparing or removing services can be released" in e for e in errors))

    def test_unknown_fields_rejected(self) -> None:
        errors = _action_type("delete").validate_values("metapool", {"action": "reset"})
        self.assertTrue(any("unknown fields: action" in e for e in errors))


class MetaPoolAssignmentUpdateValidationTest(FlowTestCase):
    def test_valid_reassignment_passes(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        self.assertEqual(
            _action_type("update").validate_values(
                "metapool", {"user_service": row.uuid, "user": self.other.uuid}, meta_pool
            ),
            [],
        )

    def test_user_field_is_required(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        errors = _action_type("update").validate_values("metapool", {"user_service": row.uuid}, meta_pool)
        self.assertTrue(any("field user" in e for e in errors))

    def test_unknown_user_rejected(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        errors = _action_type("update").validate_values(
            "metapool",
            {"user_service": row.uuid, "user": "00000000-0000-0000-0000-000000000000"},
            meta_pool,
        )
        self.assertTrue(any("does not exist" in e for e in errors))

    def test_duplicate_owner_within_the_same_member_pool_rejected(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        _assigned(member_pool, self.other)
        errors = _action_type("update").validate_values(
            "metapool", {"user_service": row.uuid, "user": self.other.uuid}, meta_pool
        )
        self.assertTrue(any("already owns another user service" in e for e in errors))

    def test_same_user_in_another_member_pool_is_accepted(self) -> None:
        # The one-service-per-user rule is per member pool service: the
        # same user may own services of different member pools at once
        authenticator = create_db_authenticator()
        groups = create_db_groups(authenticator, 1)
        service_a = create_db_service(create_db_provider())
        service_b = create_db_service(create_db_provider())
        pool_a = create_db_servicepool(service=service_a, osmanager=create_db_osmanager())
        pool_b = create_db_servicepool(service=service_b, osmanager=create_db_osmanager())
        meta_pool = create_db_metapool([pool_a, pool_b], groups)
        _assigned(pool_a, self.owner)
        row_b = _assigned(pool_b, self.other)
        self.assertEqual(
            _action_type("update").validate_values(
                "metapool", {"user_service": row_b.uuid, "user": self.owner.uuid}, meta_pool
            ),
            [],
        )

    def test_only_usable_or_preparing_can_be_reassigned(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner, state=State.REMOVING)
        errors = _action_type("update").validate_values(
            "metapool", {"user_service": row.uuid, "user": self.other.uuid}, meta_pool
        )
        self.assertTrue(any("reassign" in e for e in errors))

    def test_must_be_assigned_not_cached(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        row.cache_level = types.services.CacheLevel.L1
        row.save(update_fields=["cache_level"])
        errors = _action_type("update").validate_values(
            "metapool", {"user_service": row.uuid, "user": self.other.uuid}, meta_pool
        )
        self.assertTrue(any("not an assigned service of this meta pool" in e for e in errors))


class MetaPoolAssignmentFingerprintTest(FlowTestCase):
    def test_fingerprint_tracks_interesting_rows_and_ownership(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        action_type = _action_type("custom")
        self.assertEqual(action_type.snapshot_values(meta_pool, ["user_services"]), {"user_services": []})
        base = action_type.fingerprint(meta_pool)
        self.assertEqual(base, action_type.fingerprint(meta_pool))

        _assigned(member_pool, self.owner)
        # A usable row is not in the CAS states (it is not disappearing/errored)
        self.assertEqual(base, action_type.fingerprint(meta_pool))
        # A canceled (info state) row is interesting
        canceled = _assigned(member_pool, self.other, state=State.CANCELED)
        self.assertNotEqual(base, action_type.fingerprint(meta_pool))
        snapshot = action_type.snapshot_values(meta_pool, ["user_services"])["user_services"]
        self.assertEqual([row["uuid"] for row in snapshot], [canceled.uuid])
        # So is its owner: update runs DENY on owner drift anywhere in the projection
        fingerprint_with_other = action_type.fingerprint(meta_pool)
        canceled.user = self.owner
        canceled.save(update_fields=["user"])
        self.assertNotEqual(fingerprint_with_other, action_type.fingerprint(meta_pool))
        canceled.state = State.USABLE
        canceled.save(update_fields=["state"])
        self.assertEqual(base, action_type.fingerprint(meta_pool))


class MetaPoolAssignmentExecuteTest(FlowTestCase):
    def _run(
        self,
        operation: str,
        meta_pool: models.MetaPool,
        values: dict[str, typing.Any],
        *,
        force_supported: bool = False,
    ) -> mock.MagicMock:
        action = self._action(
            self._flow(),
            action_type=f"metapool.assignment.{operation}",
            target_uuid=meta_pool.uuid,
            values=values,
            base_values={},
            base_etag="etag",
        )
        proxy_cls = mock.MagicMock()
        proxy_cls.return_value.execute = mock.AsyncMock(return_value=None)
        with mock.patch("uds.mutability.types.meta_pools.assignment.RestProxy", proxy_cls):
            if force_supported:
                with _reset_supported:
                    summary = async_to_sync(_action_type(operation).execute)(action, request=make_request())
            else:
                summary = async_to_sync(_action_type(operation).execute)(action, request=make_request())
        self._last_summary = summary
        return proxy_cls

    def test_reset_posts_the_named_custom_method(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        proxy_cls = self._run(
            "custom", meta_pool, {"action": "reset", "user_service": row.uuid}, force_supported=True
        )
        call = proxy_cls.return_value.execute.await_args_list[0]
        target = call[0][0]
        self.assertIs(target.handler, MetaAssignedService)
        self.assertEqual(target.path, "meta_pools/{uuid}/services")
        self.assertEqual(target.method, types.rest.CustomMethodMethod.POST)
        self.assertEqual(target.args, (row.uuid, "reset"))
        self.assertIs(target.parent.handler, MetaPools)
        self.assertEqual(call[1]["parent_uuid"], meta_pool.uuid)
        self.assertIn("resetting", self._last_summary)

    def test_reset_aborts_when_unsupported_at_execution(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        with self.assertRaises(rest_exceptions.NotSupportedError):
            self._run("custom", meta_pool, {"action": "reset", "user_service": row.uuid})

    def test_execution_revalidates_an_unknown_verb(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        with self.assertRaises(rest_exceptions.RequestError):
            self._run("custom", meta_pool, {"action": "reboot", "user_service": row.uuid})

    def test_reset_aborts_when_the_member_vanished(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        uuid_value = row.uuid
        row.delete()
        with self.assertRaises(rest_exceptions.RequestError):
            self._run(
                "custom", meta_pool, {"action": "reset", "user_service": uuid_value}, force_supported=True
            )

    def test_reset_aborts_when_state_advanced(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        row.state = State.PREPARING
        row.save(update_fields=["state"])
        with self.assertRaises(rest_exceptions.RequestError):
            self._run("custom", meta_pool, {"action": "reset", "user_service": row.uuid}, force_supported=True)

    def test_release_deletes_the_item(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        proxy_cls = self._run("delete", meta_pool, {"user_service": row.uuid})
        call = proxy_cls.return_value.execute.await_args_list[0]
        target = call[0][0]
        self.assertIs(target.handler, MetaAssignedService)
        self.assertEqual(target.method, types.rest.CustomMethodMethod.DELETE)
        self.assertEqual(target.args, (row.uuid,))
        self.assertIn("releasing", self._last_summary)

    def test_release_aborts_when_state_advanced(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        row.state = State.REMOVED
        row.save(update_fields=["state"])
        with self.assertRaises(rest_exceptions.RequestError):
            self._run("delete", meta_pool, {"user_service": row.uuid})

    def test_update_puts_the_owner_change(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        proxy_cls = self._run("update", meta_pool, {"user_service": row.uuid, "user": self.other.uuid})
        call = proxy_cls.return_value.execute.await_args_list[0]
        target = call[0][0]
        self.assertIs(target.handler, MetaAssignedService)
        self.assertEqual(target.path, "meta_pools/{uuid}/services")
        self.assertEqual(target.method, types.rest.CustomMethodMethod.PUT)
        self.assertEqual(target.args, (row.uuid,))
        self.assertIs(target.parent.handler, MetaPools)
        params = call[0][2]
        self.assertEqual(params["user_id"], self.other.uuid)
        self.assertEqual(params["auth_id"], self.other.manager.uuid)
        self.assertEqual(call[1]["parent_uuid"], meta_pool.uuid)
        self.assertIn("ownership reassigned", self._last_summary)

    def test_update_aborts_when_the_member_vanished(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        uuid_value = row.uuid
        user_uuid = self.other.uuid
        row.delete()
        with self.assertRaises(rest_exceptions.RequestError):
            self._run("update", meta_pool, {"user_service": uuid_value, "user": user_uuid})

    def test_update_aborts_when_state_advanced(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        row.state = State.REMOVING
        row.save(update_fields=["state"])
        with self.assertRaises(rest_exceptions.RequestError):
            self._run("update", meta_pool, {"user_service": row.uuid, "user": self.other.uuid})

    def test_update_aborts_on_duplicate_owner(self) -> None:
        meta_pool = _metapool()
        member_pool = _first_member_pool(meta_pool)
        row = _assigned(member_pool, self.owner)
        _assigned(member_pool, self.other)
        with self.assertRaises(rest_exceptions.RequestError):
            self._run("update", meta_pool, {"user_service": row.uuid, "user": self.other.uuid})
