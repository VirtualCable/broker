"""M2M relation action types: ``metapool.groups``, ``servicepool.groups``,
``servicepool.transports`` (option A over pure many-to-many sets)."""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_types, get as registry_get
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


def _servicepool() -> models.ServicePool:
    service = create_db_service(create_db_provider())
    return create_db_servicepool(service=service, osmanager=create_db_osmanager())


def _groups(count: int) -> list[models.Group]:
    return list(create_db_groups(create_db_authenticator(), count))


class M2MRegistryTest(FlowTestCase):
    def test_all_registered(self) -> None:
        for type_id, cls in (
            ("metapool.groups", MetaPoolGroups),
            ("servicepool.groups", ServicePoolGroups),
            ("servicepool.transports", ServicePoolTransports),
        ):
            self.assertIs(registry_get(type_id), cls)
        self.assertTrue(
            {"metapool.groups", "servicepool.groups", "servicepool.transports"}
            <= {t.type_id for t in all_types()}
        )

    def test_resolve_unknown_target_is_not_found(self) -> None:
        for cls in (MetaPoolGroups, ServicePoolGroups, ServicePoolTransports):
            with self.assertRaises(rest_exceptions.NotFound):
                cls().resolve_target(_UNSET_UUID)

    def test_are_target_scoped(self) -> None:
        for cls in (MetaPoolGroups, ServicePoolGroups, ServicePoolTransports):
            self.assertIs(cls.target_scoped_fields, True)


class M2MFieldsTest(FlowTestCase):
    def test_groups_have_no_choices_transports_do(self) -> None:
        pool = _servicepool()
        transport = create_db_transport()

        groups_def = ServicePoolGroups().field_definitions("servicepool", pool)[0]
        self.assertEqual(groups_def["type"], types.ui.FieldType.MULTICHOICE.value)
        self.assertNotIn("choices", groups_def)

        transports_def = ServicePoolTransports().field_definitions("servicepool", pool)[0]
        self.assertEqual(transports_def["choices"], [{"value": transport.uuid, "label": transport.name}])

    def test_valid_sets(self) -> None:
        pool = _servicepool()
        action_type = ServicePoolGroups()
        group = _groups(1)[0]
        self.assertEqual(action_type.validate_values("servicepool", {"groups": [group.uuid]}, pool), [])
        self.assertEqual(action_type.validate_values("servicepool", {"groups": []}, pool), [])
        # empty set through the rpc path (values must be a non-empty dict)
        self.assertEqual(ServicePoolTransports().validate_values("servicepool", {"transports": []}, pool), [])

    def test_missing_field_rejected(self) -> None:
        errors = MetaPoolGroups().validate_values("metapool", {"other": 1})
        self.assertTrue(any("unknown fields" in e for e in errors))
        self.assertTrue(any("groups is required" in e for e in errors))

    def test_member_errors(self) -> None:
        pool = _servicepool()
        group = _groups(1)[0]
        action_type = ServicePoolGroups()
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
        errors = ServicePoolTransports().validate_values("servicepool", {"transports": [group.uuid]}, pool)
        self.assertTrue(any("does not match any existing transport" in e for e in errors))
        errors = ServicePoolGroups().validate_values("servicepool", {"groups": [transport.uuid]}, pool)
        self.assertTrue(any("does not match any existing group" in e for e in errors))


class M2MCasTest(FlowTestCase):
    def test_snapshot_fingerprint_and_drift(self) -> None:
        pool = _servicepool()
        first, second = _groups(2)
        pool.assignedGroups.add(first)
        action_type = ServicePoolGroups()

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
        self.assertEqual(ServicePoolTransports().snapshot_values(pool, ["transports"]), {"transports": []})


class M2MExecuteTest(FlowTestCase):
    def _run(
        self,
        action_type: typing.Any,
        target: models.ServicePool | models.MetaPool,
        values: dict[str, typing.Any],
    ) -> mock.MagicMock:
        action = self._action(
            self._flow(),
            action_type=action_type.type_id,
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

        proxy_cls = self._run(ServicePoolGroups(), pool, {"groups": [kept.uuid, added.uuid]})

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
        proxy_cls = self._run(ServicePoolGroups(), pool, {"groups": [group.uuid]})
        proxy_cls.return_value.execute.assert_not_awaited()
        self.assertIn("0 added, 0 removed", self._summary)

    def test_empty_set_removes_all(self) -> None:
        pool = _servicepool()
        first, second = _groups(2)
        pool.assignedGroups.add(first, second)
        proxy_cls = self._run(ServicePoolGroups(), pool, {"groups": []})
        self.assertEqual(proxy_cls.return_value.execute.await_count, 2)
        for call in proxy_cls.return_value.execute.await_args_list:
            self.assertEqual(call[0][0].method, types.rest.CustomMethodMethod.DELETE)
        self.assertIn("2 removed", self._summary)

    def test_metapool_groups_uses_metapools_surface(self) -> None:
        pool = _servicepool()
        assigned = _groups(1)[0]
        meta_pool = create_db_metapool([pool], [assigned])
        # starts with one group assigned, propose none
        proxy_cls = self._run(MetaPoolGroups(), meta_pool, {"groups": []})
        call = proxy_cls.return_value.execute.await_args_list[0]
        self.assertEqual(call[0][0].path, "meta_pools/{uuid}/groups")
        self.assertIs(call[0][0].parent.handler, MetaPools)
        self.assertEqual(call[0][0].args, (assigned.uuid,))

    def test_transports_attach_detach(self) -> None:
        pool = _servicepool()
        attached, detached = create_db_transport(), create_db_transport()
        new = create_db_transport()
        pool.transports.add(attached, detached)
        proxy_cls = self._run(ServicePoolTransports(), pool, {"transports": [attached.uuid, new.uuid]})
        calls = proxy_cls.return_value.execute.await_args_list
        self.assertEqual(calls[0][0][0].method, types.rest.CustomMethodMethod.DELETE)
        self.assertEqual(calls[0][0][0].args, (detached.uuid,))
        self.assertEqual(calls[1][0][0].method, types.rest.CustomMethodMethod.POST)
        self.assertEqual(calls[1][0][2], {"id": new.uuid})
        self.assertIs(calls[0][0][0].handler, Transports)
