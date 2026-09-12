"""``network.update``: registration, discovery, CAS and execution shape."""

from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_types, get as registry_get
from uds.mutability.types.networks import NetworkUpdate
from uds.models import Network

from tests.mcp.mutability._helpers import FlowTestCase, make_request

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


class NetworkUpdateRegistryTest(FlowTestCase):
    def test_network_update_is_registered(self) -> None:
        found = registry_get("network.update")
        self.assertIs(found, NetworkUpdate)
        self.assertIn("network.update", [t.type_id for t in all_types()])

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            NetworkUpdate().resolve_target("00000000-0000-0000-0000-000000000000")


class NetworkUpdateFieldsTest(FlowTestCase):
    def test_field_definitions_match_what_the_handler_saves(self) -> None:
        names = [d["name"] for d in NetworkUpdate().field_definitions("network")]
        # The stock comments field of the gui is NOT persisted by the
        # handler, so it is not offered as mutable
        self.assertEqual(sorted(names), ["name", "net_string", "tags"])

    def test_snapshot_and_fingerprint_track_the_range(self) -> None:
        network = Network.objects.create(name="net1", net_string="10.0.0.0/24")
        action_type = NetworkUpdate()

        self.assertEqual(
            action_type.snapshot_values(network, ["name", "net_string"]),
            {"name": "net1", "net_string": "10.0.0.0/24"},
        )
        base = action_type.fingerprint(network)
        self.assertEqual(base, action_type.fingerprint(network))

        network.net_string = "192.168.0.0/16"
        network.save(update_fields=["net_string"])
        self.assertNotEqual(base, action_type.fingerprint(network))


class NetworkUpdateExecuteTest(FlowTestCase):
    def test_execute_sends_rest_put_shape(self) -> None:
        network = Network.objects.create(name="net1", net_string="10.0.0.0/24")
        action = self._action(
            self._flow(),
            action_type="network.update",
            target_uuid=network.uuid,
            values={"net_string": "192.168.0.0/16"},
            base_values={"net_string": "10.0.0.0/24"},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types.networks.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            # async_to_sync mirrors the production executor: the
            # thread-sensitive ORM work runs on the caller thread
            summary = async_to_sync(NetworkUpdate().execute)(action, request=make_request())

        self.assertIn("updated", summary)
        execute_call = proxy_cls.return_value.execute.call_args
        from uds.REST.methods.networks import Networks

        self.assertIs(execute_call[0][0].handler, Networks)
        self.assertEqual(execute_call[0][2], {"net_string": "192.168.0.0/16"})
