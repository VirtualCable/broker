"""``servicepool.assignable.create``: hand one inventory element to a user.

The family proposes the ``create_from_assignable`` custom method on a
pool: one assigned user service bound to one inventory element of the
pool's service (``enumerate_assignables``) and one user, created under
MANAGEMENT over the pool (the proposal targets the pool uuid; no subtype
gallery). Propose-time validation mirrors the stable manager gates (the
service type supports manual assignment, the user exists, the element is
in the live inventory, the pool has the publication the type needs); the
asynchronous outcome (a row that starts PREPARING and may still be failed
by the provider) belongs to REST, not to the proposal.
"""

import collections.abc
import contextlib
import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.util.model import sql_now
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.base import ActionOperation, MutableActionType, StalePolicy
from uds.mutability.types.service_pools.assignable import ServicePoolAssignable
from uds.REST.methods.services_pools import ServicesPools

from tests.fixtures.modules.service.service import TestServiceNoCache
from tests.mcp.mutability._helpers import FlowTestCase, make_request

State = types.states.State


def _pool() -> models.ServicePool:
    from tests.fixtures.services import create_db_provider, create_db_service, create_db_servicepool

    service = create_db_service(create_db_provider(), use_caching_version=False)
    return create_db_servicepool(service)


@contextlib.contextmanager
def _assignable(inventory: collections.abc.Sequence[str]) -> collections.abc.Generator[None]:
    """Make the pool's (non-assignable) test service type behave assignable."""
    choices = [types.ui.ChoiceItem(item, f"Machine {item}") for item in inventory]
    with (
        mock.patch.object(TestServiceNoCache, "enumerate_assignables", return_value=choices),
        mock.patch.object(TestServiceNoCache, "assign_from_assignables", return_value=State.PREPARING),
    ):
        yield


def _action_type() -> MutableActionType:
    factory = registry_get("servicepool.assignable.create")
    assert factory is not None
    return factory()


class ServicePoolAssignableRegistryTest(FlowTestCase):
    def test_create_operation_is_registered(self) -> None:
        self.assertIs(type(_action_type()), ServicePoolAssignable)
        self.assertIn("servicepool.assignable.create", all_type_ids())
        self.assertNotIn("servicepool.assignable.update", all_type_ids())
        self.assertNotIn("servicepool.assignable.delete", all_type_ids())
        self.assertEqual(ServicePoolAssignable.supported_operations(), frozenset({ActionOperation.CREATE}))

    def test_create_declaration(self) -> None:
        self.assertIs(ServicePoolAssignable.create_needs_parent, True)
        self.assertIs(ServicePoolAssignable.create_has_gallery, False)

    def test_is_not_readable_on_its_own(self) -> None:
        # The inventory has its own curated read (get_servicepool_assignables)
        self.assertFalse(ServicePoolAssignable.readable())

    def test_keeps_the_create_cas_exemption(self) -> None:
        # The family creates only, and stores exempt creations from the CAS
        # entirely (no base at proposal time, no drift check at execution):
        # nothing is declared to bypass the default.
        self.assertIs(_action_type().get_stale_policy(), StalePolicy.DENY)

    def test_resolve_unknown_pool_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            _action_type().resolve_target("00000000-0000-0000-0000-000000000000")

    def test_publishes_a_propose_tool(self) -> None:
        action_type = _action_type()
        self.assertIn("inventory element", action_type.tool_title().lower())
        self.assertIn("get_servicepool_assignables", action_type.tool_description())
        self.assertIn("PREPARING", action_type.tool_description())


class ServicePoolAssignableFieldsTest(FlowTestCase):
    def test_create_publishes_user_id_and_assignable_id(self) -> None:
        pool = _pool()
        defs = _action_type().create_field_definitions("servicepool", pool)
        self.assertEqual([d["name"] for d in defs], ["user_id", "assignable_id"])
        self.assertTrue(all(d["type"] == types.ui.FieldType.TEXT.value for d in defs))

    def test_assignable_tooltip_lists_the_live_inventory(self) -> None:
        pool = _pool()
        with _assignable(["m-1", "m-2"]):
            defs = _action_type().create_field_definitions("servicepool", pool)
        self.assertIn("m-1", defs[1]["tooltip"])
        self.assertIn("m-2", defs[1]["tooltip"])

    def test_assignable_tooltip_for_a_non_assignable_pool(self) -> None:
        pool = _pool()
        defs = _action_type().create_field_definitions("servicepool", pool)
        # No choices leak; the tooltip points at the discovery tool
        self.assertNotIn("choices", defs[1])
        self.assertIn("get_servicepool_assignables", defs[1]["tooltip"])

    def test_empty_inventory_reports_no_assignables(self) -> None:
        pool = _pool()
        with _assignable([]):
            defs = _action_type().create_field_definitions("servicepool", pool)
        self.assertIn("no assignable inventory", defs[1]["tooltip"])

    def test_field_definitions_alias_the_create_surface(self) -> None:
        pool = _pool()
        action_type = _action_type()
        self.assertEqual(
            action_type.field_definitions("servicepool", pool),
            action_type.create_field_definitions("servicepool", pool),
        )


class ServicePoolAssignableValidationTest(FlowTestCase):
    def test_valid_assignment_passes(self) -> None:
        pool = _pool()
        with _assignable(["m-1"]):
            self.assertEqual(
                _action_type().create_validate_values(
                    "servicepool", {"user_id": self.owner.uuid, "assignable_id": "m-1"}, pool
                ),
                [],
            )

    def test_user_id_is_required(self) -> None:
        pool = _pool()
        with _assignable(["m-1"]):
            errors = _action_type().create_validate_values("servicepool", {"assignable_id": "m-1"}, pool)
        self.assertTrue(any("user_id" in e for e in errors))

    def test_assignable_id_is_required(self) -> None:
        pool = _pool()
        with _assignable(["m-1"]):
            errors = _action_type().create_validate_values("servicepool", {"user_id": self.owner.uuid}, pool)
        self.assertTrue(any("assignable_id: required" in e for e in errors))

    def test_unknown_user_rejected(self) -> None:
        pool = _pool()
        with _assignable(["m-1"]):
            errors = _action_type().create_validate_values(
                "servicepool", {"user_id": "00000000-0000-0000-0000-000000000000", "assignable_id": "m-1"}, pool
            )
        self.assertTrue(any("does not exist" in e for e in errors))

    def test_malformed_user_uuid_rejected(self) -> None:
        pool = _pool()
        with _assignable(["m-1"]):
            errors = _action_type().create_validate_values(
                "servicepool", {"user_id": "not-a-uuid", "assignable_id": "m-1"}, pool
            )
        self.assertTrue(any("not a valid uuid" in e for e in errors))

    def test_non_assignable_service_type_rejected(self) -> None:
        pool = _pool()  # no _assignable patch: TestServiceNoCache is not assignable
        errors = _action_type().create_validate_values(
            "servicepool", {"user_id": self.owner.uuid, "assignable_id": "m-1"}, pool
        )
        self.assertTrue(any("does not support assigning inventory" in e for e in errors))

    def test_assignable_absent_from_inventory_rejected(self) -> None:
        pool = _pool()
        with _assignable(["m-1"]):
            errors = _action_type().create_validate_values(
                "servicepool", {"user_id": self.owner.uuid, "assignable_id": "ghost"}, pool
            )
        self.assertTrue(any("'ghost' is not in the pool's inventory" in e for e in errors))

    def test_unknown_fields_rejected(self) -> None:
        pool = _pool()
        with _assignable(["m-1"]):
            errors = _action_type().create_validate_values(
                "servicepool", {"user_id": self.owner.uuid, "assignable_id": "m-1", "count": 2}, pool
            )
        self.assertTrue(any("unknown fields: count" in e for e in errors))

    def test_publication_required_when_the_type_uses_it(self) -> None:
        pool = _pool()
        with (
            _assignable(["m-1"]),
            mock.patch.object(TestServiceNoCache, "publication_type", object()),
        ):
            # The pool has no active publication yet
            errors = _action_type().create_validate_values(
                "servicepool", {"user_id": self.owner.uuid, "assignable_id": "m-1"}, pool
            )
        self.assertTrue(any("no active publication" in e for e in errors))
        with (
            _assignable(["m-1"]),
            mock.patch.object(TestServiceNoCache, "publication_type", object()),
            mock.patch.object(models.ServicePool, "active_publication", return_value=mock.MagicMock()),
        ):
            self.assertEqual(
                _action_type().create_validate_values(
                    "servicepool", {"user_id": self.owner.uuid, "assignable_id": "m-1"}, pool
                ),
                [],
            )


class ServicePoolAssignableFingerprintTest(FlowTestCase):
    def test_fingerprint_tracks_assigned_rows(self) -> None:
        pool = _pool()
        action_type = _action_type()
        self.assertEqual(action_type.snapshot_values(pool, ["user_services"]), {"user_services": []})
        base = action_type.fingerprint(pool)
        self.assertEqual(base, action_type.fingerprint(pool))

        publication = models.ServicePoolPublication.objects.create(
            deployed_service=pool,
            publish_date=sql_now(),
            state_date=sql_now(),
            state=State.USABLE,
            revision=pool.current_pub_revision,
        )
        from tests.fixtures.services import create_db_userservice

        create_db_userservice(pool, publication, self.owner)
        self.assertNotEqual(base, action_type.fingerprint(pool))
        snapshot = action_type.snapshot_values(pool, ["user_services"])["user_services"]
        self.assertEqual([row["user"] for row in snapshot], [self.owner.uuid])

    def test_other_names_snapshot_empty(self) -> None:
        pool = _pool()
        self.assertEqual(_action_type().snapshot_values(pool, ["something_else"]), {})


class ServicePoolAssignableExecuteTest(FlowTestCase):
    def _run(self, pool: models.ServicePool, values: dict[str, typing.Any]) -> mock.MagicMock:
        action = self._action(
            self._flow(),
            action_type="servicepool.assignable.create",
            target_uuid=pool.uuid,
            values=values,
            base_values={},
            base_etag="",
        )
        proxy_cls = mock.MagicMock()
        proxy_cls.return_value.execute = mock.AsyncMock(return_value={"id": "new-us-uuid"})
        with mock.patch("uds.mutability.verbs.RestProxy", proxy_cls):
            summary = async_to_sync(_action_type().execute)(action, request=make_request())
        self._last_summary = summary
        return proxy_cls

    def test_create_posts_the_master_custom_method(self) -> None:
        pool = _pool()
        with _assignable(["m-1"]):
            proxy_cls = self._run(pool, {"user_id": self.owner.uuid, "assignable_id": "m-1"})
        call = proxy_cls.return_value.execute.await_args_list[0]
        target = call[0][0]
        self.assertIs(target.handler, ServicesPools)
        self.assertEqual(target.path, "services_pools")
        self.assertEqual(target.method, types.rest.CustomMethodMethod.POST)
        self.assertEqual(target.args, (pool.uuid, "create_from_assignable"))
        params = call[0][2]
        self.assertEqual(params, {"user_id": self.owner.uuid, "assignable_id": "m-1"})
        self.assertIn("m-1", self._last_summary)
        self.assertIn("new-us-uuid", self._last_summary)

    def test_execution_revalidates_the_service_type(self) -> None:
        pool = _pool()  # not assignable (no _assignable patch)
        with self.assertRaises(rest_exceptions.RequestError):
            self._run(pool, {"user_id": self.owner.uuid, "assignable_id": "m-1"})

    def test_execution_revalidates_the_user(self) -> None:
        pool = _pool()
        with _assignable(["m-1"]), self.assertRaises(rest_exceptions.RequestError):
            self._run(pool, {"user_id": "00000000-0000-0000-0000-000000000000", "assignable_id": "m-1"})
