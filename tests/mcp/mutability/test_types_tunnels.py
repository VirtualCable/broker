"""``tunnel.update`` / ``.create`` / ``.delete``: registration, type filter, validation and execution shape."""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds import models
from uds.core import types
from uds.core.consts.mcp import CREATE_TARGET_UUID
from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.base import ActionOperation, StalePolicy
from uds.mutability.types.tunnels import TunnelUpdate
from uds.REST.methods.tunnels_management import Tunnels

from tests.mcp.mutability._helpers import FlowTestCase, build_action, make_request

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false

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
        assert found is not None
        self.assertIs(type(found()), TunnelUpdate)
        self.assertIn("tunnel.update", all_type_ids())

    def test_supported_operations_derive_from_hooks(self) -> None:
        self.assertEqual(
            TunnelUpdate.supported_operations(),
            frozenset({ActionOperation.UPDATE, ActionOperation.CREATE, ActionOperation.DELETE}),
        )

    def test_delete_rides_the_strict_deny_policy(self) -> None:
        delete = TunnelUpdate(ActionOperation.DELETE)
        self.assertEqual(delete.full_id, "tunnel.delete")
        self.assertEqual(delete.get_stale_policy(), StalePolicy.DENY)

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
        self.assertEqual(sorted(names), ["comments", "host", "name", "port", "tags"])
        # Servers: relations outside the PUT form
        for name in ("servers", "type", "subtype"):
            self.assertNotIn(name, names)
        errors = TunnelUpdate().validate_values("tunnel", {"assigned_servers": ["a"]})
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
            params,
            {
                "name": tunnel.name,
                "comments": tunnel.comments,
                "tags": [],
                "host": "new.example.com",
                "port": 8443,
            },
        )


class TunnelCreateDeleteTest(FlowTestCase):
    """tunnel.create / tunnel.delete: the root verbs."""

    def test_create_requires_name_and_host(self) -> None:
        create = TunnelUpdate(ActionOperation.CREATE)
        errors = create.create_validate_values("tunnel", {})
        self.assertTrue(any("name" in e for e in errors))
        self.assertTrue(any("host" in e for e in errors))
        # No data_type required: tunnels have no subtype gallery
        self.assertEqual(create.create_validate_values("tunnel", {"name": "t", "host": "10.0.0.1"}), [])
        # An invalid host/port still fails at propose time
        self.assertTrue(
            any(
                "host" in e
                for e in create.create_validate_values("tunnel", {"name": "t", "host": "bad host!!"})
            )
        )
        self.assertTrue(
            any(
                "port" in e
                for e in create.create_validate_values(
                    "tunnel", {"name": "t", "host": "10.0.0.1", "port": 99999}
                )
            )
        )

    def test_create_execution_posts_the_form_shape(self) -> None:
        create = TunnelUpdate(ActionOperation.CREATE)
        action = build_action(
            action_type="tunnel.create",
            target_uuid=CREATE_TARGET_UUID,
            values={"name": "new tunnel", "host": "10.0.0.1", "port": 8443},
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value={"id": "tn-uuid"})
            summary = async_to_sync(create.execute)(action, request=make_request())
            target, _request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual((target.handler, target.method.value, target.args), (Tunnels, "POST", ()))
            self.assertEqual(
                params,
                {"name": "new tunnel", "comments": "", "tags": [], "host": "10.0.0.1", "port": 8443},
            )
        self.assertIn("new tunnel", summary)
        self.assertIn("tn-uuid", summary)

    def test_create_omitted_port_rides_the_gui_default(self) -> None:
        create = TunnelUpdate(ActionOperation.CREATE)
        action = build_action(
            action_type="tunnel.create",
            target_uuid=CREATE_TARGET_UUID,
            values={"name": "new tunnel", "host": "tunnel.example.com"},
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value=None)
            summary = async_to_sync(create.execute)(action, request=make_request())
            _target, _request, params = proxy_cls.return_value.execute.call_args[0]
            # the gui default 443, never the handler's optional 0
            self.assertEqual(params["port"], 443)
        self.assertIn("created", summary)
        self.assertNotIn("uuid", summary)

    def test_delete_execution_sends_canonical_delete(self) -> None:
        tunnel = _create_tunnel()
        delete = TunnelUpdate(ActionOperation.DELETE)
        action = build_action(
            action_type="tunnel.delete",
            target_uuid=tunnel.uuid,
            values={},
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock()
            summary = async_to_sync(delete.execute)(action, request=make_request())
            target, _request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual(
                (target.handler, target.method.value, target.args),
                (Tunnels, "DELETE", (tunnel.uuid,)),
            )
            self.assertEqual(params, {})
        self.assertIn(tunnel.name, summary)
        self.assertIn("deleted", summary)
