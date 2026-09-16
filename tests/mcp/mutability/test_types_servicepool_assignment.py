"""``servicepool.assignment``: update, custom and delete over assigned services.

``update`` changes the owner of one assigned user service through the PUT
of the REST services detail (one user service per user and pool) and runs
the strict DENY CAS policy; ``custom`` applies one named verb (today only
``reset``) through its custom method — verbs are data in the ``action``
CHOICE field, so the family stays open for future ones without new tools
— and ``delete`` releases the service through the DELETE on the item,
both under FORCE (their outcome is asynchronous; REST holds the
authoritative checks at execution time). ``read`` mirrors the assigned
collection plus the capability flags and the verbs the type supports.
Resetting is gated by the pool's ``can_reset`` capability.
"""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.util.model import sql_now
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.base import ActionOperation, MutableActionType, StalePolicy
from uds.mutability.types.service_pools import assignment as assignment_module
from uds.mutability.types.service_pools.assignment import ServicePoolAssignment
from uds.REST.methods.services_pools import ServicesPools
from uds.REST.methods.user_services import AssignedUserService

from tests.mcp.mutability._helpers import FlowTestCase, make_request

State = types.states.State

RESET = assignment_module.RESET


def _pool() -> models.ServicePool:
    from tests.fixtures.services import (
        create_db_osmanager,
        create_db_provider,
        create_db_service,
        create_db_servicepool,
    )

    service = create_db_service(create_db_provider())
    return create_db_servicepool(service=service, osmanager=create_db_osmanager())


def _publication(pool: models.ServicePool) -> models.ServicePoolPublication:
    now = sql_now()
    return models.ServicePoolPublication.objects.create(
        deployed_service=pool,
        publish_date=now,
        state_date=now,
        state=State.USABLE,
        revision=pool.current_pub_revision,
    )


def _assigned(
    pool: models.ServicePool,
    user: models.User,
    state: str = State.USABLE,
) -> models.UserService:
    from tests.fixtures.services import create_db_userservice

    row = create_db_userservice(pool, _publication(pool), user)
    if state != row.state:
        row.state = state
        row.save(update_fields=["state"])
    return row


def _action_type(operation: str) -> MutableActionType:
    factory = registry_get(f"servicepool.assignment.{operation}")
    assert factory is not None
    return factory()


_supported = mock.patch.object(
    assignment_module,
    "_supported_actions",
    return_value=[RESET],
)


class ServicePoolAssignmentRegistryTest(FlowTestCase):
    def test_all_operations_are_registered(self) -> None:
        for operation in ("update", "custom", "delete"):
            self.assertIs(type(_action_type(operation)), ServicePoolAssignment)
            self.assertIn(f"servicepool.assignment.{operation}", all_type_ids())
        self.assertNotIn("servicepool.assignment.add", all_type_ids())
        self.assertEqual(
            ServicePoolAssignment.supported_operations(),
            frozenset({ActionOperation.UPDATE, ActionOperation.CUSTOM, ActionOperation.DELETE}),
        )

    def test_read_override_publishes_a_read(self) -> None:
        self.assertTrue(ServicePoolAssignment.readable())

    def test_stale_policy_is_force_for_async_operations_and_deny_for_update(self) -> None:
        self.assertIs(_action_type("custom").get_stale_policy(), StalePolicy.FORCE)
        self.assertIs(_action_type("delete").get_stale_policy(), StalePolicy.FORCE)
        self.assertIs(_action_type("update").get_stale_policy(), StalePolicy.DENY)

    def test_is_target_scoped(self) -> None:
        self.assertIs(ServicePoolAssignment.target_scoped_fields, True)

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            _action_type("custom").resolve_target("00000000-0000-0000-0000-000000000000")

    def test_read_view_publishes_assigned_services_and_verbs(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        view = ServicePoolAssignment().read(pool.uuid)
        self.assertEqual(view["entity_type"], "servicepool.assignment")
        self.assertEqual(view["target_uuid"], pool.uuid)
        self.assertIn("can_reset", view["capabilities"])
        # TestServiceCache.can_reset is False: reset is not offered
        self.assertEqual(view["supported_actions"], [])
        by_uuid = {item["uuid"]: item for item in view["user_services"]}
        self.assertEqual(by_uuid[row.uuid]["state"], State.USABLE)
        self.assertEqual(by_uuid[row.uuid]["owner"], self.owner.pretty_name)
        self.assertEqual(by_uuid[row.uuid]["owner_uuid"], self.owner.uuid)


class ServicePoolAssignmentFieldsTest(FlowTestCase):
    def test_custom_publishes_action_and_user_service(self) -> None:
        defs = _action_type("custom").field_definitions("servicepool")
        self.assertEqual([d["name"] for d in defs], ["action", "user_service"])
        self.assertEqual(defs[0]["type"], types.ui.FieldType.CHOICE.value)
        self.assertEqual(defs[0]["choices"], ["reset"])

    def test_delete_publishes_only_the_user_service_field(self) -> None:
        defs = _action_type("delete").field_definitions("servicepool")
        self.assertEqual([d["name"] for d in defs], ["user_service"])

    def test_update_publishes_user_service_and_user(self) -> None:
        defs = _action_type("update").field_definitions("servicepool")
        self.assertEqual([d["name"] for d in defs], ["user_service", "user"])
        self.assertIn("at most one user service", defs[1]["tooltip"])
        self.assertIn("list_authenticators_users", defs[1]["tooltip"])

    def test_custom_choices_follow_the_pool_capability(self) -> None:
        pool = _pool()
        defs = _action_type("custom").field_definitions("servicepool", pool)
        self.assertEqual(defs[0]["choices"], [])
        with _supported:
            defs = _action_type("custom").field_definitions("servicepool", pool)
        self.assertEqual(defs[0]["choices"], ["reset"])
        self.assertIn("supports: reset", defs[0]["tooltip"])

    def test_custom_tooltip_reports_unsupported_reset(self) -> None:
        pool = _pool()
        defs = _action_type("custom").field_definitions("servicepool", pool)
        self.assertIn("supports none of these actions", defs[0]["tooltip"])


class ServicePoolAssignmentCustomValidationTest(FlowTestCase):
    def test_action_field_is_required(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        errors = _action_type("custom").validate_values("servicepool", {"user_service": row.uuid}, pool)
        self.assertTrue(any("action is required" in e for e in errors))

    def test_unknown_action_rejected(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        errors = _action_type("custom").validate_values(
            "servicepool", {"action": "reboot", "user_service": row.uuid}, pool
        )
        self.assertTrue(any("'reboot' is not supported" in e for e in errors))

    def test_action_must_be_a_string(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        errors = _action_type("custom").validate_values(
            "servicepool", {"action": 5, "user_service": row.uuid}, pool
        )
        self.assertTrue(any("action must be one of the action literals" in e for e in errors))

    def test_reset_requires_the_can_reset_capability(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        errors = _action_type("custom").validate_values(
            "servicepool", {"action": "reset", "user_service": row.uuid}, pool
        )
        self.assertTrue(any("does not support the 'reset' action" in e for e in errors))

    def test_valid_reset_when_supported(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        with _supported:
            self.assertEqual(
                _action_type("custom").validate_values(
                    "servicepool", {"action": "reset", "user_service": row.uuid}, pool
                ),
                [],
            )

    def test_only_usable_services_can_be_reset(self) -> None:
        pool = _pool()
        preparing = _assigned(pool, self.owner, state=State.PREPARING)
        with _supported:
            errors = _action_type("custom").validate_values(
                "servicepool", {"action": "reset", "user_service": preparing.uuid}, pool
            )
        self.assertTrue(any("only usable services can be reset" in e for e in errors))

    def test_must_be_an_assigned_service_of_the_pool(self) -> None:
        pool = _pool()
        other = _assigned(_pool(), self.owner)
        with _supported:
            errors = _action_type("custom").validate_values(
                "servicepool", {"action": "reset", "user_service": other.uuid}, pool
            )
        self.assertTrue(any("is not an assigned service" in e for e in errors))

    def test_user_service_field_is_required(self) -> None:
        pool = _pool()
        errors = _action_type("custom").validate_values("servicepool", {"action": "reset"}, pool)
        self.assertTrue(any("user_service is required" in e for e in errors))

    def test_malformed_uuid_rejected(self) -> None:
        pool = _pool()
        errors = _action_type("delete").validate_values("servicepool", {"user_service": "not-a-uuid"}, pool)
        self.assertTrue(any("not a valid uuid" in e for e in errors))
        errors = _action_type("delete").validate_values("servicepool", {"user_service": 7}, pool)
        self.assertTrue(any("must be the uuid string" in e for e in errors))


class ServicePoolAssignmentDeleteValidationTest(FlowTestCase):
    def test_release_needs_no_capability_and_accepts_usable(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        self.assertEqual(
            _action_type("delete").validate_values("servicepool", {"user_service": row.uuid}, pool), []
        )

    def test_release_accepts_preparing_and_removing(self) -> None:
        pool = _pool()
        for state in (State.PREPARING, State.REMOVING):
            row = _assigned(pool, self.owner, state=state)
            self.assertEqual(
                _action_type("delete").validate_values("servicepool", {"user_service": row.uuid}, pool),
                [],
            )

    def test_release_rejects_removable(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner, state=State.REMOVABLE)
        errors = _action_type("delete").validate_values("servicepool", {"user_service": row.uuid}, pool)
        self.assertTrue(any("only usable, preparing or removing services can be released" in e for e in errors))

    def test_unknown_fields_rejected(self) -> None:
        errors = _action_type("delete").validate_values("servicepool", {"action": "reset"})
        self.assertTrue(any("unknown fields: action" in e for e in errors))


class ServicePoolAssignmentUpdateValidationTest(FlowTestCase):
    def test_valid_reassignment_passes(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        self.assertEqual(
            _action_type("update").validate_values(
                "servicepool", {"user_service": row.uuid, "user": self.other.uuid}, pool
            ),
            [],
        )

    def test_user_field_is_required(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        errors = _action_type("update").validate_values("servicepool", {"user_service": row.uuid}, pool)
        self.assertTrue(any("field user" in e for e in errors))

    def test_unknown_user_rejected(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        errors = _action_type("update").validate_values(
            "servicepool",
            {"user_service": row.uuid, "user": "00000000-0000-0000-0000-000000000000"},
            pool,
        )
        self.assertTrue(any("does not exist" in e for e in errors))

    def test_malformed_user_uuid_rejected(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        errors = _action_type("update").validate_values(
            "servicepool", {"user_service": row.uuid, "user": "not-a-uuid"}, pool
        )
        self.assertTrue(any("not a valid uuid" in e for e in errors))

    def test_duplicate_owner_rejected(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        other_row = _assigned(pool, self.other)
        errors = _action_type("update").validate_values(
            "servicepool", {"user_service": row.uuid, "user": self.other.uuid}, pool
        )
        self.assertTrue(any("already owns another user service" in e for e in errors))
        # Reassigning to the very same owner of the row itself is a no-op, allowed
        self.assertEqual(
            _action_type("update").validate_values(
                "servicepool", {"user_service": other_row.uuid, "user": self.other.uuid}, pool
            ),
            [],
        )

    def test_only_usable_or_preparing_can_be_reassigned(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner, state=State.REMOVING)
        errors = _action_type("update").validate_values(
            "servicepool", {"user_service": row.uuid, "user": self.other.uuid}, pool
        )
        self.assertTrue(any("reassign" in e for e in errors))

    def test_must_be_an_assigned_service_of_the_pool(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        row.cache_level = types.services.CacheLevel.L1
        row.save(update_fields=["cache_level"])
        errors = _action_type("update").validate_values(
            "servicepool", {"user_service": row.uuid, "user": self.other.uuid}, pool
        )
        self.assertTrue(any("not an assigned service" in e for e in errors))


class ServicePoolAssignmentFingerprintTest(FlowTestCase):
    def test_fingerprint_tracks_interesting_assigned_rows(self) -> None:
        pool = _pool()
        action_type = _action_type("custom")
        self.assertEqual(action_type.snapshot_values(pool, ["user_services"]), {"user_services": []})
        base = action_type.fingerprint(pool)
        self.assertEqual(base, action_type.fingerprint(pool))

        _assigned(pool, self.owner)
        # A usable row is not in the CAS states (it is not disappearing/errored)
        self.assertEqual(base, action_type.fingerprint(pool))
        # A canceled (info state) row is interesting
        canceled = _assigned(pool, self.other, state=State.CANCELED)
        self.assertNotEqual(base, action_type.fingerprint(pool))
        snapshot = action_type.snapshot_values(pool, ["user_services"])["user_services"]
        self.assertEqual([row["uuid"] for row in snapshot], [canceled.uuid])
        # So is its owner: update runs DENY on owner drift anywhere in the pool
        fingerprint_with_other = action_type.fingerprint(pool)
        canceled.user = self.owner
        canceled.save(update_fields=["user"])
        self.assertNotEqual(fingerprint_with_other, action_type.fingerprint(pool))
        canceled.state = State.USABLE
        canceled.save(update_fields=["state"])
        self.assertEqual(base, action_type.fingerprint(pool))


class ServicePoolAssignmentExecuteTest(FlowTestCase):
    def _run(
        self,
        operation: str,
        pool: models.ServicePool,
        values: dict[str, typing.Any],
        *,
        force_supported: bool = False,
    ) -> mock.MagicMock:
        action = self._action(
            self._flow(),
            action_type=f"servicepool.assignment.{operation}",
            target_uuid=pool.uuid,
            values=values,
            base_values={},
            base_etag="etag",
        )
        proxy_cls = mock.MagicMock()
        proxy_cls.return_value.execute = mock.AsyncMock(return_value=None)
        patchers = [mock.patch("uds.mutability.types.service_pools.assignment.RestProxy", proxy_cls)]
        if force_supported:
            patchers.append(
                mock.patch.object(assignment_module, "_supported_actions", return_value=[RESET]),
            )
        with patchers[0]:
            if force_supported:
                with patchers[1]:
                    summary = async_to_sync(_action_type(operation).execute)(action, request=make_request())
            else:
                summary = async_to_sync(_action_type(operation).execute)(action, request=make_request())
        self._last_summary = summary
        return proxy_cls

    def test_reset_posts_the_named_custom_method(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        proxy_cls = self._run(
            "custom", pool, {"action": "reset", "user_service": row.uuid}, force_supported=True
        )
        call = proxy_cls.return_value.execute.await_args_list[0]
        target = call[0][0]
        self.assertIs(target.handler, AssignedUserService)
        self.assertEqual(target.path, "services_pools/{uuid}/services")
        self.assertEqual(target.method, types.rest.CustomMethodMethod.POST)
        self.assertEqual(target.args, (row.uuid, "reset"))
        self.assertIs(target.parent.handler, ServicesPools)
        self.assertEqual(call[1]["parent_uuid"], pool.uuid)
        self.assertIn("resetting", self._last_summary)

    def test_reset_aborts_when_unsupported_at_execution(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        with self.assertRaises(rest_exceptions.NotSupportedError):
            self._run("custom", pool, {"action": "reset", "user_service": row.uuid})

    def test_execution_revalidates_an_unknown_verb(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        with self.assertRaises(rest_exceptions.RequestError):
            self._run("custom", pool, {"action": "reboot", "user_service": row.uuid})

    def test_reset_aborts_when_the_member_vanished(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        uuid_value = row.uuid
        row.delete()
        with self.assertRaises(rest_exceptions.RequestError):
            self._run("custom", pool, {"action": "reset", "user_service": uuid_value}, force_supported=True)

    def test_reset_aborts_when_state_advanced(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        row.state = State.PREPARING
        row.save(update_fields=["state"])
        with self.assertRaises(rest_exceptions.RequestError):
            self._run("custom", pool, {"action": "reset", "user_service": row.uuid}, force_supported=True)

    def test_release_deletes_the_item(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        proxy_cls = self._run("delete", pool, {"user_service": row.uuid})
        call = proxy_cls.return_value.execute.await_args_list[0]
        target = call[0][0]
        self.assertIs(target.handler, AssignedUserService)
        self.assertEqual(target.method, types.rest.CustomMethodMethod.DELETE)
        self.assertEqual(target.args, (row.uuid,))
        self.assertIn("releasing", self._last_summary)

    def test_release_aborts_when_the_member_vanished(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        uuid_value = row.uuid
        row.delete()
        with self.assertRaises(rest_exceptions.RequestError):
            self._run("delete", pool, {"user_service": uuid_value})

    def test_release_aborts_when_state_advanced(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        row.state = State.REMOVED
        row.save(update_fields=["state"])
        with self.assertRaises(rest_exceptions.RequestError):
            self._run("delete", pool, {"user_service": row.uuid})

    def test_update_puts_the_owner_change(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        proxy_cls = self._run("update", pool, {"user_service": row.uuid, "user": self.other.uuid})
        call = proxy_cls.return_value.execute.await_args_list[0]
        target = call[0][0]
        self.assertIs(target.handler, AssignedUserService)
        self.assertEqual(target.path, "services_pools/{uuid}/services")
        self.assertEqual(target.method, types.rest.CustomMethodMethod.PUT)
        self.assertEqual(target.args, (row.uuid,))
        self.assertIs(target.parent.handler, ServicesPools)
        params = call[0][2]
        self.assertEqual(params["user_id"], self.other.uuid)
        self.assertEqual(params["auth_id"], self.other.manager.uuid)
        self.assertEqual(call[1]["parent_uuid"], pool.uuid)
        self.assertIn("ownership reassigned", self._last_summary)

    def test_update_aborts_when_the_member_vanished(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        uuid_value = row.uuid
        user_uuid = self.other.uuid
        row.delete()
        with self.assertRaises(rest_exceptions.RequestError):
            self._run("update", pool, {"user_service": uuid_value, "user": user_uuid})

    def test_update_aborts_when_state_advanced(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        row.state = State.REMOVING
        row.save(update_fields=["state"])
        with self.assertRaises(rest_exceptions.RequestError):
            self._run("update", pool, {"user_service": row.uuid, "user": self.other.uuid})

    def test_update_aborts_when_the_user_vanished(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        user_uuid = self.other.uuid
        self.other.delete()
        with self.assertRaises(rest_exceptions.RequestError):
            self._run("update", pool, {"user_service": row.uuid, "user": user_uuid})

    def test_update_aborts_on_duplicate_owner(self) -> None:
        pool = _pool()
        row = _assigned(pool, self.owner)
        _assigned(pool, self.other)
        with self.assertRaises(rest_exceptions.RequestError):
            self._run("update", pool, {"user_service": row.uuid, "user": self.other.uuid})
