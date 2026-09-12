"""server_group.update / server.update action types: discovery, CAS and canonical PUT."""

import asyncio
import typing
from unittest import mock

from uds.REST.methods.servers_management import ServersGroups, ServersServers
from uds.core import types
from uds.mcp.rest_proxy import RestProxy
from uds.mutability.types.servers import ServerGroupUpdate, ServerUpdate

from tests.fixtures.servers import create_server, create_server_group
from tests.mcp.mutability._helpers import build_action
from tests.utils import rest

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


class ServerGroupUpdateTypeTest(rest.test.RESTTestCase):
    """server_group.update: fields, CAS and canonical execution."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.group = create_server_group(num_servers=1)
        self.action_type = ServerGroupUpdate()

    def _for_type(self) -> str:
        return self.action_type.for_type_of(self.group)

    def test_field_definitions_cover_name_comments_tags_and_weights(self) -> None:
        defs = self.action_type.field_definitions(self._for_type(), self.group)
        names = [d["name"] for d in defs]
        for expected in (
            "name",
            "comments",
            "tags",
            "weights_cpu",
            "weights_memory",
            "weights_users",
            "weights_max_expected_users",
        ):
            self.assertIn(expected, names)
        # Info fields (title) are not part of the mutable surface
        self.assertNotIn("title", names)

    def test_validate_values_accepts_known_fields(self) -> None:
        errors = self.action_type.validate_values(
            self._for_type(), {"name": "x", "weights_cpu": 40}, self.group
        )
        self.assertEqual(errors, [])

    def test_validate_values_rejects_unknown_fields_with_accepted_list(self) -> None:
        errors = self.action_type.validate_values(self._for_type(), {"zzz": 1}, self.group)
        self.assertEqual(len(errors), 1)
        self.assertIn("zzz", errors[0])
        self.assertIn("accepted", errors[0])

    def test_snapshot_values_reads_current_state(self) -> None:
        snapshot = self.action_type.snapshot_values(self.group, ["name", "comments", "tags", "weights_cpu"])
        self.assertEqual(snapshot["name"], self.group.name)
        self.assertEqual(snapshot["comments"], self.group.comments)
        self.assertEqual(snapshot["tags"], [])
        self.assertEqual(snapshot["weights_cpu"], int(self.group.weights.cpu * 100 + 0.5))

    def test_fingerprint_is_stable_and_tracks_changes(self) -> None:
        values = {"name": "whatever"}
        _, base_etag = self.action_type.snapshot_and_fingerprint(self.group, values)
        _, base_etag_again = self.action_type.snapshot_and_fingerprint(self.group, values)
        self.assertEqual(base_etag, base_etag_again)

        self.group.name = "changed by a human"
        self.group.save()
        _, new_etag = self.action_type.snapshot_and_fingerprint(self.group, values)
        self.assertNotEqual(base_etag, new_etag)

    def test_diff_detects_stale_base_and_changed_item(self) -> None:
        values = {"name": "proposed name"}
        base_values, base_etag = self.action_type.snapshot_and_fingerprint(self.group, values)
        action = build_action(
            action_type="server_group.update",
            target_uuid=self.group.uuid,
            values=values,
            base_values=base_values,
            base_etag=base_etag,
        )

        diff = self.action_type.diff(action)
        self.assertEqual(diff["stale_fields"], [])
        self.assertFalse(diff["item_changed"])

        self.group.name = "changed by a human"
        self.group.save()
        diff = self.action_type.diff(action)
        self.assertIn("name", diff["stale_fields"])
        self.assertTrue(diff["item_changed"])

    def test_execute_builds_canonical_put_with_data_type(self) -> None:
        action = build_action(
            action_type="server_group.update",
            target_uuid=self.group.uuid,
            values={"name": "new-name", "weights_cpu": 50},
            base_values={"name": "old"},
            base_etag="x",
        )
        request = mock.MagicMock()
        with (
            mock.patch("uds.mutability.types.servers.RestProxy") as proxy_cls,
            mock.patch.object(self.action_type, "resolve_target", return_value=self.group),
        ):
            proxy_cls.return_value.execute = mock.AsyncMock(return_value=None)
            asyncio.run(self.action_type.execute(action, request))
            proxy_cls.return_value.execute.assert_awaited_once()
            await_args = proxy_cls.return_value.execute.await_args
            assert await_args is not None
            target, called_request, params = await_args[0]
            self.assertEqual(target.handler, ServersGroups)
            self.assertIsNone(target.parent)
            self.assertEqual(target.method.value, "PUT")
            self.assertEqual(target.args, (self.group.uuid,))
            self.assertEqual(called_request, request)
            # data_type is injected from the target (never mutable)
            self.assertEqual(params["data_type"], self._for_type())
            self.assertEqual(params["name"], "new-name")


class ServerUpdateTypeTest(rest.test.RESTTestCase):
    """server.update: target-scoped unmanaged surface, parent permissions, canonical detail PUT."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.group = create_server_group(type=types.servers.ServerType.UNMANAGED, num_servers=1)
        server = self.group.servers.first()
        assert server is not None
        self.server = server
        self.action_type = ServerUpdate()

    def test_field_definitions_refuse_bare_type(self) -> None:
        self.assertTrue(ServerUpdate.target_scoped_fields)
        with self.assertRaises(ValueError):
            self.action_type.field_definitions(self.action_type.for_type_of(self.server))

    def test_field_definitions_cover_unmanaged_fields(self) -> None:
        defs = self.action_type.field_definitions(self.action_type.for_type_of(self.server), self.server)
        self.assertEqual([d["name"] for d in defs], ["hostname", "ip", "mac"])

    def test_managed_servers_expose_no_mutable_surface(self) -> None:
        managed_group = create_server_group()  # ServerType.SERVER
        managed = managed_group.servers.first()
        assert managed is not None
        self.assertEqual(self.action_type.field_definitions(self.action_type.for_type_of(managed), managed), [])
        self.assertEqual(self.action_type.etag_fields(self.action_type.for_type_of(managed), managed), [])

    def test_permission_target_is_the_parent_group(self) -> None:
        self.assertEqual(self.action_type.permission_target(self.server), self.group)

    def test_permission_target_without_group_is_not_found(self) -> None:
        lone = create_server()  # server outside any group
        with self.assertRaises(Exception):
            self.action_type.permission_target(lone)

    def test_snapshot_values_reads_current_state_and_masks_null_mac(self) -> None:
        snapshot = self.action_type.snapshot_values(self.server, ["hostname", "ip", "mac"])
        self.assertEqual(snapshot["hostname"], self.server.hostname)
        self.assertEqual(snapshot["ip"], self.server.ip)
        self.assertEqual(snapshot["mac"], "")  # fixture leaves NULL_MAC

    def test_fingerprint_is_stable_and_tracks_changes(self) -> None:
        values = {"hostname": "whatever"}
        _, base_etag = self.action_type.snapshot_and_fingerprint(self.server, values)
        _, base_etag_again = self.action_type.snapshot_and_fingerprint(self.server, values)
        self.assertEqual(base_etag, base_etag_again)

        self.server.hostname = "changed by a human"
        self.server.save()
        _, new_etag = self.action_type.snapshot_and_fingerprint(self.server, values)
        self.assertNotEqual(base_etag, new_etag)

    def test_execute_builds_canonical_detail_put_with_full_body(self) -> None:
        action = build_action(
            action_type="server.update",
            target_uuid=self.server.uuid,
            values={"hostname": "new-host"},
            base_values={"hostname": "old"},
            base_etag="x",
        )
        request = mock.MagicMock()
        with (
            mock.patch.object(RestProxy, "_execute_sync") as exec_sync,
            mock.patch.object(self.action_type, "resolve_target", return_value=self.server),
        ):
            exec_sync.return_value = None
            asyncio.run(self.action_type.execute(action, request))
            exec_sync.assert_called_once()
            target, called_request, params, parent_uuid = exec_sync.call_args[0]
            self.assertEqual(target.handler, ServersServers)
            self.assertEqual(target.parent.handler, ServersGroups)
            self.assertEqual(target.method.value, "PUT")
            self.assertEqual(target.args, (self.server.uuid,))
            self.assertEqual(target.path, "servers/groups/{uuid}/servers")
            self.assertEqual(called_request, request)
            self.assertEqual(parent_uuid, self.group.uuid)
            # The detail PUT does not merge: every mutable field is sent,
            # proposed ones on top of the live untouched ones
            self.assertEqual(params["hostname"], "new-host")
            self.assertEqual(params["ip"], self.server.ip)
            self.assertIn("mac", params)


class ServerRegistrationTest(rest.test.RESTTestCase):
    """The registry exposes the new types alongside the existing ones."""

    def test_servers_types_are_registered(self) -> None:
        from uds.mutability import registry

        self.assertIsNotNone(registry.get("server_group.update"))
        self.assertIsNotNone(registry.get("server.update"))
