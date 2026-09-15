"""M2M relation action types: ``metapool.groups``, ``servicepool.groups``,
``servicepool.transports`` (set/add/delete/get over pure many-to-many sets)."""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.base import ActionOperation, StalePolicy
from uds.mutability.types.meta_pools import MetaPoolGroups
from uds.mutability.types.service_pools import ServicePoolGroups, ServicePoolTransports
from uds.REST.methods.meta_pools import MetaPools
from uds.REST.methods.services_pools import ServicesPools
from uds.REST.methods.user_services import Groups as AssignedGroups
from uds.REST.methods.user_services import Transports

from tests.fixtures.authenticators import create_db_authenticator, create_db_groups
from tests.fixtures.services import (
    create_db_metapool,
    create_db_osmanager,
    create_db_provider,
    create_db_service,
    create_db_servicepool,
    create_db_transport,
)
from tests.mcp.mutability._helpers import FlowTestCase, make_request

_UNSET_UUID = "00000000-0000-0000-0000-000000000000"


def _bound(cls: typing.Any, operation: ActionOperation = ActionOperation.SET) -> typing.Any:
    """Instance of one family bound to one operation (as the registry makes them)."""
    factory = registry_get(f"{cls.type_id}.{operation.as_str()}")
    assert factory is not None
    return factory()


def _servicepool() -> models.ServicePool:
    service = create_db_service(create_db_provider())
    return create_db_servicepool(service=service, osmanager=create_db_osmanager())


def _groups(count: int) -> list[models.Group]:
    return list(create_db_groups(create_db_authenticator(), count))


class M2MRegistryTest(FlowTestCase):
    def test_all_registered(self) -> None:
        for type_id, cls in (
            ("metapool.groups.set", MetaPoolGroups),
            ("servicepool.groups.set", ServicePoolGroups),
            ("servicepool.transports.set", ServicePoolTransports),
        ):
            found = registry_get(type_id)
            assert found is not None
            self.assertIs(type(found()), cls)
        self.assertTrue(
            {"metapool.groups.set", "servicepool.groups.set", "servicepool.transports.set"}
            <= set(all_type_ids())
        )

    def test_every_family_exposes_the_four_operations(self) -> None:
        for cls in (MetaPoolGroups, ServicePoolGroups, ServicePoolTransports):
            for operation in (
                ActionOperation.SET,
                ActionOperation.ADD,
                ActionOperation.DELETE,
                ActionOperation.GET,
            ):
                full_id = f"{cls.type_id}.{operation.as_str()}"
                self.assertIn(full_id, all_type_ids())
                instance = _bound(cls, operation)
                self.assertIsInstance(instance, cls)
                self.assertIs(instance.operation, operation)
                self.assertEqual(instance.full_id, full_id)
                self.assertEqual(instance.proposable, operation is not ActionOperation.GET)

    def test_multi_op_family_needs_an_operation(self) -> None:
        with self.assertRaises(ValueError):
            ServicePoolGroups().validate_values("servicepool", {"groups": []})

    def test_delta_operations_are_force_set_is_deny(self) -> None:
        self.assertEqual(_bound(ServicePoolGroups).get_stale_policy(), StalePolicy.DENY)
        self.assertEqual(_bound(ServicePoolGroups, ActionOperation.ADD).get_stale_policy(), StalePolicy.FORCE)
        self.assertEqual(
            _bound(ServicePoolGroups, ActionOperation.DELETE).get_stale_policy(), StalePolicy.FORCE
        )

    def test_resolve_unknown_target_is_not_found(self) -> None:
        for cls in (MetaPoolGroups, ServicePoolGroups, ServicePoolTransports):
            with self.assertRaises(rest_exceptions.NotFound):
                _bound(cls).resolve_target(_UNSET_UUID)

    def test_are_target_scoped(self) -> None:
        for cls in (MetaPoolGroups, ServicePoolGroups, ServicePoolTransports):
            self.assertIs(cls.target_scoped_fields, True)


class M2MFieldsTest(FlowTestCase):
    def test_groups_have_no_choices_transports_do(self) -> None:
        pool = _servicepool()
        transport = create_db_transport()

        groups_def = _bound(ServicePoolGroups).field_definitions("servicepool", pool)[0]
        self.assertEqual(groups_def["type"], types.ui.FieldType.MULTICHOICE.value)
        self.assertNotIn("choices", groups_def)

        transports_def = _bound(ServicePoolTransports).field_definitions("servicepool", pool)[0]
        self.assertEqual(transports_def["choices"], [{"value": transport.uuid, "label": transport.name}])

    def test_tooltip_follows_the_operation(self) -> None:
        pool = _servicepool()
        for operation, fragment in (
            (ActionOperation.SET, "Complete desired set"),
            (ActionOperation.ADD, "attach"),
            (ActionOperation.DELETE, "detach"),
            (ActionOperation.GET, "read view"),
        ):
            definition = _bound(ServicePoolGroups, operation).field_definitions("servicepool", pool)[0]
            self.assertIn(fragment, definition["tooltip"], f"{operation}: {definition['tooltip']}")

    def test_valid_sets(self) -> None:
        pool = _servicepool()
        action_type = _bound(ServicePoolGroups)
        group = _groups(1)[0]
        self.assertEqual(action_type.validate_values("servicepool", {"groups": [group.uuid]}, pool), [])
        self.assertEqual(action_type.validate_values("servicepool", {"groups": []}, pool), [])
        # empty set through the rpc path (values must be a non-empty dict)
        self.assertEqual(
            _bound(ServicePoolTransports).validate_values("servicepool", {"transports": []}, pool), []
        )

    def test_delta_operations_reject_empty_lists(self) -> None:
        pool = _servicepool()
        for operation in (ActionOperation.ADD, ActionOperation.DELETE):
            errors = _bound(ServicePoolGroups, operation).validate_values("servicepool", {"groups": []}, pool)
            self.assertTrue(any("at least one uuid" in e for e in errors), f"{operation}: {errors}")

    def test_missing_field_rejected(self) -> None:
        errors = _bound(MetaPoolGroups).validate_values("metapool", {"other": 1})
        self.assertTrue(any("unknown fields" in e for e in errors))
        self.assertTrue(any("groups is required" in e for e in errors))

    def test_member_errors(self) -> None:
        pool = _servicepool()
        group = _groups(1)[0]
        action_type = _bound(ServicePoolGroups)
        cases: list[tuple[typing.Any, str]] = [
            (["no-uuid"], "not a valid uuid"),
            ([_UNSET_UUID], "does not match any existing group"),
            ([123], "must be the uuid string"),
            ([group.uuid, group.uuid.upper()], "duplicates"),
            ("not-a-list", "must be a list"),
        ]
        for value, fragment in cases:
            errors = action_type.validate_values("servicepool", {"groups": value}, pool)
            self.assertTrue(any(fragment in e for e in errors), f"{value!r} -> {errors}")

    def test_wrong_universe_rejected(self) -> None:
        # transports must exist as transports, groups as groups
        pool = _servicepool()
        group = _groups(1)[0]
        transport = create_db_transport()
        errors = _bound(ServicePoolTransports).validate_values(
            "servicepool", {"transports": [group.uuid]}, pool
        )
        self.assertTrue(any("does not match any existing transport" in e for e in errors))
        errors = _bound(ServicePoolGroups).validate_values("servicepool", {"groups": [transport.uuid]}, pool)
        self.assertTrue(any("does not match any existing group" in e for e in errors))


class M2MCasTest(FlowTestCase):
    def test_snapshot_fingerprint_and_drift(self) -> None:
        pool = _servicepool()
        first, second = _groups(2)
        pool.assignedGroups.add(first)
        action_type = _bound(ServicePoolGroups)

        self.assertEqual(action_type.snapshot_values(pool, ["groups"]), {"groups": [first.uuid]})
        base = action_type.fingerprint(pool)
        self.assertEqual(base, action_type.fingerprint(pool))

        pool.assignedGroups.add(second)
        self.assertNotEqual(base, action_type.fingerprint(pool))
        self.assertEqual(
            sorted(action_type.snapshot_values(pool, ["groups"])["groups"]), sorted([first.uuid, second.uuid])
        )

    def test_empty_set_snapshot(self) -> None:
        pool = _servicepool()
        self.assertEqual(
            _bound(ServicePoolTransports).snapshot_values(pool, ["transports"]), {"transports": []}
        )


class M2MReadTest(FlowTestCase):
    def test_get_returns_current_members(self) -> None:
        pool = _servicepool()
        attached, other = _groups(2)
        pool.assignedGroups.add(attached)
        del other
        view = _bound(ServicePoolGroups, ActionOperation.GET).read(pool.uuid)
        self.assertEqual(view["action_type"], "servicepool.groups.get")
        self.assertEqual(view["current_values"], {"groups": [attached.uuid]})
        self.assertEqual(view["current_members"], [{"uuid": attached.uuid, "name": attached.name}])

    def test_write_bindings_refuse_read(self) -> None:
        pool = _servicepool()
        with self.assertRaises(NotImplementedError):
            _bound(ServicePoolGroups).read(pool.uuid)


class M2MExecuteTest(FlowTestCase):
    def _run(
        self,
        action_type: typing.Any,
        target: models.ServicePool | models.MetaPool,
        values: dict[str, typing.Any],
    ) -> mock.MagicMock:
        action = self._action(
            self._flow(),
            action_type=action_type.full_id,
            target_uuid=target.uuid,
            values=values,
            base_values={},
            base_etag="etag",
        )
        proxy_cls = mock.MagicMock()
        proxy_cls.return_value.execute = mock.AsyncMock(return_value=None)
        with mock.patch("uds.mutability.types._relations.RestProxy", proxy_cls):
            self._summary = async_to_sync(action_type.execute)(action, request=make_request())
        return proxy_cls

    def test_diff_delete_then_add(self) -> None:
        pool = _servicepool()
        kept, dropped = _groups(2)
        added = _groups(1)[0]
        pool.assignedGroups.add(kept, dropped)

        proxy_cls = self._run(_bound(ServicePoolGroups), pool, {"groups": [kept.uuid, added.uuid]})

        calls = proxy_cls.return_value.execute.await_args_list
        self.assertEqual(
            [call[0][0].method for call in calls],
            [types.rest.CustomMethodMethod.DELETE, types.rest.CustomMethodMethod.POST],
        )
        for call in calls:
            target = call[0][0]
            self.assertIs(target.handler, AssignedGroups)
            self.assertEqual(target.path, "services_pools/{uuid}/groups")
            self.assertIs(target.parent.handler, ServicesPools)
            self.assertEqual(call[1]["parent_uuid"], pool.uuid)
        # DELETE addresses the member positionally; POST create carries
        # the id in params and no positional arg (args would dispatch as
        # a custom method)
        delete_call, create_call = calls
        self.assertEqual(delete_call[0][0].args, (dropped.uuid,))
        self.assertEqual(delete_call[0][2], {})
        self.assertEqual(create_call[0][0].args, ())
        self.assertEqual(create_call[0][2], {"id": added.uuid})
        self.assertIn("1 added, 1 removed", self._summary)

    def test_equal_set_is_a_noop(self) -> None:
        pool = _servicepool()
        group = _groups(1)[0]
        pool.assignedGroups.add(group)
        proxy_cls = self._run(_bound(ServicePoolGroups), pool, {"groups": [group.uuid]})
        proxy_cls.return_value.execute.assert_not_awaited()
        self.assertIn("0 added, 0 removed", self._summary)

    def test_empty_set_removes_all(self) -> None:
        pool = _servicepool()
        first, second = _groups(2)
        pool.assignedGroups.add(first, second)
        proxy_cls = self._run(_bound(ServicePoolGroups), pool, {"groups": []})
        self.assertEqual(proxy_cls.return_value.execute.await_count, 2)
        for call in proxy_cls.return_value.execute.await_args_list:
            self.assertEqual(call[0][0].method, types.rest.CustomMethodMethod.DELETE)
        self.assertIn("2 removed", self._summary)

    def test_add_is_a_delta_touching_only_new_members(self) -> None:
        pool = _servicepool()
        attached, untouched = _groups(2)
        new = _groups(1)[0]
        pool.assignedGroups.add(attached, untouched)
        # attached again (idempotent, skipped) plus the new one
        proxy_cls = self._run(
            _bound(ServicePoolGroups, ActionOperation.ADD), pool, {"groups": [attached.uuid, new.uuid]}
        )
        calls = proxy_cls.return_value.execute.await_args_list
        self.assertEqual([call[0][0].method for call in calls], [types.rest.CustomMethodMethod.POST])
        self.assertEqual(calls[0][0][2], {"id": new.uuid})
        self.assertIn("1 added, 0 removed", self._summary)

    def test_add_noop_when_all_present(self) -> None:
        pool = _servicepool()
        attached = _groups(1)[0]
        pool.assignedGroups.add(attached)
        proxy_cls = self._run(_bound(ServicePoolGroups, ActionOperation.ADD), pool, {"groups": [attached.uuid]})
        proxy_cls.return_value.execute.assert_not_awaited()
        self.assertIn("0 added, 0 removed", self._summary)

    def test_delete_is_a_delta_touching_only_present_members(self) -> None:
        pool = _servicepool()
        attached, other = _groups(2)
        absent = _groups(1)[0]
        pool.assignedGroups.add(attached, other)
        del other
        # absent is not attached: skipped; attached is removed
        proxy_cls = self._run(
            _bound(ServicePoolGroups, ActionOperation.DELETE), pool, {"groups": [attached.uuid, absent.uuid]}
        )
        calls = proxy_cls.return_value.execute.await_args_list
        self.assertEqual([call[0][0].method for call in calls], [types.rest.CustomMethodMethod.DELETE])
        self.assertEqual(calls[0][0][0].args, (attached.uuid,))
        self.assertIn("0 added, 1 removed", self._summary)

    def test_metapool_groups_uses_metapools_surface(self) -> None:
        pool = _servicepool()
        assigned = _groups(1)[0]
        meta_pool = create_db_metapool([pool], [assigned])
        # starts with one group assigned, propose none
        proxy_cls = self._run(_bound(MetaPoolGroups), meta_pool, {"groups": []})
        call = proxy_cls.return_value.execute.await_args_list[0]
        self.assertEqual(call[0][0].path, "meta_pools/{uuid}/groups")
        self.assertIs(call[0][0].parent.handler, MetaPools)
        self.assertEqual(call[0][0].args, (assigned.uuid,))

    def test_transports_attach_detach(self) -> None:
        pool = _servicepool()
        attached, detached = create_db_transport(), create_db_transport()
        new = create_db_transport()
        pool.transports.add(attached, detached)
        proxy_cls = self._run(_bound(ServicePoolTransports), pool, {"transports": [attached.uuid, new.uuid]})
        calls = proxy_cls.return_value.execute.await_args_list
        self.assertEqual(calls[0][0][0].method, types.rest.CustomMethodMethod.DELETE)
        self.assertEqual(calls[0][0][0].args, (detached.uuid,))
        self.assertEqual(calls[1][0][0].method, types.rest.CustomMethodMethod.POST)
        self.assertEqual(calls[1][0][2], {"id": new.uuid})
        self.assertIs(calls[0][0][0].handler, Transports)
