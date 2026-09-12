"""``service_pool_group.update``: registration, discovery, CAS, execution."""

from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_types, get as registry_get
from uds.mutability.types.pool_groups import ServicePoolGroupUpdate
from uds.models import ServicePoolGroup
from uds.REST.methods.services_pool_groups import ServicesPoolGroups

from tests.mcp.mutability._helpers import FlowTestCase, make_request

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


class ServicePoolGroupUpdateTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("service_pool_group.update")
        self.assertIs(found, ServicePoolGroupUpdate)
        self.assertIn("service_pool_group.update", [t.type_id for t in all_types()])

    def test_field_definitions_exclude_the_image_fk(self) -> None:
        names = [d["name"] for d in ServicePoolGroupUpdate().field_definitions("service_pool_group")]
        self.assertEqual(sorted(names), ["comments", "name", "priority"])

    def test_snapshot_and_fingerprint_track_priority(self) -> None:
        pool_group = ServicePoolGroup.objects.create(name="lab", priority=5)
        action_type = ServicePoolGroupUpdate()

        snapshot = action_type.snapshot_values(pool_group, ["name", "priority"])
        self.assertEqual(snapshot, {"name": "lab", "priority": 5})

        base = action_type.fingerprint(pool_group)
        pool_group.priority = 1
        pool_group.save(update_fields=["priority"])
        self.assertNotEqual(base, action_type.fingerprint(pool_group))

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            ServicePoolGroupUpdate().resolve_target("00000000-0000-0000-0000-000000000000")

    def test_execute_sends_rest_put_shape(self) -> None:
        pool_group = ServicePoolGroup.objects.create(name="lab", priority=5)
        action = self._action(
            self._flow(),
            action_type="service_pool_group.update",
            target_uuid=pool_group.uuid,
            values={"priority": 1},
            base_values={"priority": 5},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types.pool_groups.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            # async_to_sync mirrors the production executor: the
            # thread-sensitive ORM work runs on the caller thread
            summary = async_to_sync(ServicePoolGroupUpdate().execute)(action, request=make_request())

        self.assertIn("updated", summary)
        execute_call = proxy_cls.return_value.execute.call_args
        self.assertIs(execute_call[0][0].handler, ServicesPoolGroups)
        # The collection is "gallery" (the REST path of pool groups)
        self.assertEqual(execute_call[0][0].path, "gallery")
        self.assertEqual(execute_call[0][2], {"priority": 1})
