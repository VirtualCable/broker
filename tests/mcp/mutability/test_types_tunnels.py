"""``tunnel.update``: registration, type filter, validation and execution shape."""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_types, get as registry_get
from uds.mutability.types.tunnels import TunnelUpdate
from uds.REST.methods.tunnels_management import Tunnels

from tests.mcp.mutability._helpers import FlowTestCase, make_request

_counter = 0


def _create_tunnel(host: str = "tunnel.example.com", port: int = 443) -> models.ServerGroup:
    global _counter
    _counter += 1
    return models.ServerGroup.objects.create(
        name=f"Tunnel {_counter}",
        comments=f"Test tunnel {_counter}",
        type=types.servers.ServerType.TUNNEL.value,
        host=host,
        port=port,
    )


class TunnelUpdateRegistryTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("tunnel.update")
        self.assertIs(found, TunnelUpdate)
        self.assertIn("tunnel.update", [t.type_id for t in all_types()])

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            TunnelUpdate().resolve_target("00000000-0000-0000-0000-000000000000")

    def test_resolve_rejects_non_tunnel_groups(self) -> None:
        group = models.ServerGroup.objects.create(
            name="Plain group",
            type=types.servers.ServerType.UNMANAGED.value,
        )
        with self.assertRaises(rest_exceptions.NotFound):
            TunnelUpdate().resolve_target(group.uuid)


class TunnelUpdateFieldsTest(FlowTestCase):
    def test_mutable_fields_and_excluded_relations(self) -> None:
        defs = TunnelUpdate().field_definitions("tunnel")
        names = [d["name"] for d in defs]
        self.assertEqual(sorted(names), ["comments", "host", "name", "port"])
        # Tags: gui field the PUT drops (handler bug); servers: relations
        for name in ("tags", "servers", "type", "subtype"):
            self.assertNotIn(name, names)
        errors = TunnelUpdate().validate_values("tunnel", {"tags": ["a"]})
        self.assertTrue(any("unknown fields" in e for e in errors))

    def test_host_and_port_validated_at_propose_time(self) -> None:
        errors = TunnelUpdate().validate_values("tunnel", {"host": "not a host!!"})
        self.assertTrue(any("host" in e for e in errors))
        errors = TunnelUpdate().validate_values("tunnel", {"port": 99999})
        self.assertTrue(any("port" in e for e in errors))
        # IPs are fine, as are plain hostnames
        self.assertEqual(TunnelUpdate().validate_values("tunnel", {"host": "10.0.0.5"}), [])
        self.assertEqual(
            TunnelUpdate().validate_values("tunnel", {"host": "tunnel.example.com", "port": 8443}), []
        )

    def test_snapshot_and_fingerprint(self) -> None:
        tunnel = _create_tunnel(host="old.example.com", port=443)
        action_type = TunnelUpdate()

        snapshot = action_type.snapshot_values(tunnel, action_type.etag_fields("tunnel"))
        self.assertEqual(snapshot["host"], "old.example.com")
        self.assertEqual(snapshot["port"], 443)

        base = action_type.fingerprint(tunnel)
        self.assertEqual(base, action_type.fingerprint(tunnel))
        tunnel.port = 8443
        tunnel.save(update_fields=["port"])
        self.assertNotEqual(base, action_type.fingerprint(tunnel))


class TunnelUpdateExecuteTest(FlowTestCase):
    def test_execute_sends_full_form_with_proposed_values(self) -> None:
        tunnel = _create_tunnel(host="old.example.com", port=443)
        action = self._action(
            self._flow(),
            action_type="tunnel.update",
            target_uuid=tunnel.uuid,
            values={"host": "new.example.com", "port": 8443},
            base_values={"host": "old.example.com"},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types.tunnels.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            summary = async_to_sync(TunnelUpdate().execute)(action, request=make_request())

        self.assertIn("updated", summary)
        execute_call = proxy_cls.return_value.execute.call_args
        self.assertIs(execute_call[0][0].handler, Tunnels)
        params: dict[str, typing.Any] = execute_call[0][2]
        # The PUT is form-shaped: every saved field travels, proposed values win
        self.assertEqual(
            params, {"name": tunnel.name, "comments": tunnel.comments, "host": "new.example.com", "port": 8443}
        )
