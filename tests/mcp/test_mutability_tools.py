"""Tests for the supervised mutability MCP tools (phase 2)."""

import asyncio
import json
import typing
import unittest
from unittest import mock

from uds.REST.methods.providers import Providers
from uds.core import types
from uds.core.util import permissions
from uds.core.util.config import GlobalConfig
from uds.mcp.default_catalog import get_catalog
from uds.mcp.mutability import (
    ActionStatus,
    PendingAction,
    PendingActionStore,
    all_types,
    get as registry_get,
)
from uds.mcp.mutability.types_providers import ProviderUpdate

from tests.fixtures.authenticators import create_db_authenticator, create_db_users
from tests.fixtures.services import create_db_provider
from tests.utils import rest

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false

_MUTATION_TOOL_NAMES: typing.Final[tuple[str, ...]] = (
    "get_mutable_fields",
    "propose_provider_update",
    "list_pending_actions",
    "update_pending_action",
    "cancel_pending_action",
)


class MutabilityRegistryTest(unittest.TestCase):
    """The code-defined registry exposes provider.update."""

    def test_provider_update_is_registered(self) -> None:
        found = registry_get("provider.update")
        self.assertIsInstance(found, ProviderUpdate)
        self.assertIn("provider.update", [t.type_id for t in all_types()])

    def test_unknown_type_returns_none(self) -> None:
        self.assertIsNone(registry_get("provider.destroy_everything"))


class ProviderUpdateTypeTest(rest.test.RESTTestCase):
    """Type behavior: discovery, validation, snapshots and CAS."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.provider = create_db_provider()
        self.action_type = ProviderUpdate()

    def test_field_definitions_cover_top_level_fields(self) -> None:
        defs = self.action_type.field_definitions(self.provider.data_type)
        names = [d["name"] for d in defs]
        for expected in ("name", "comments", "tags"):
            self.assertIn(expected, names)
        for definition in defs:
            self.assertIn("secret", definition)
            self.assertIn("type", definition)

    def test_flatten_values(self) -> None:
        flat = self.action_type.flatten_values({"name": "n", "instance": {"host": "h", "port": 1}, "tags": ["t"]})
        self.assertEqual(flat, {"name": "n", "tags": ["t"], "instance.host": "h", "instance.port": 1})

    def test_validate_values_accepts_known_fields(self) -> None:
        errors = self.action_type.validate_values(self.provider.data_type, {"name": "x", "comments": "y"})
        self.assertEqual(errors, [])

    def test_validate_values_rejects_unknown_fields_with_accepted_list(self) -> None:
        errors = self.action_type.validate_values(self.provider.data_type, {"zzz": 1})
        self.assertEqual(len(errors), 1)
        self.assertIn("zzz", errors[0])
        self.assertIn("accepted", errors[0])
        self.assertIn("name", errors[0])

    def test_snapshot_values_reads_current_state(self) -> None:
        snapshot = self.action_type.snapshot_values(self.provider, ["name", "comments"])
        self.assertEqual(snapshot["name"], self.provider.name)
        self.assertEqual(snapshot["comments"], self.provider.comments)

    def test_snapshot_and_fingerprint_are_stable(self) -> None:
        values = {"name": "whatever"}
        base_values, base_etag = self.action_type.snapshot_and_fingerprint(self.provider, values)
        self.assertEqual(base_values, {"name": self.provider.name})
        _, base_etag_again = self.action_type.snapshot_and_fingerprint(self.provider, values)
        self.assertEqual(base_etag, base_etag_again)

    def test_diff_detects_stale_base_and_changed_item(self) -> None:
        values = {"name": "proposed name"}
        base_values, base_etag = self.action_type.snapshot_and_fingerprint(self.provider, values)
        action = PendingAction.create(
            type_id="provider.update",
            owner_uuid="someone",
            agent="test",
            target_uuid=self.provider.uuid,
            values=values,
            base_values=base_values,
            base_etag=base_etag,
        )

        # Untouched target: no staleness at all
        diff = self.action_type.diff(action)
        self.assertEqual(diff["stale_fields"], [])
        self.assertFalse(diff["item_changed"])

        # Someone changed the field the proposal is based on: CAS hard stop
        self.provider.name = "changed by a human"
        self.provider.save()
        diff = self.action_type.diff(action)
        self.assertIn("name", diff["stale_fields"])
        self.assertTrue(diff["item_changed"])

    def test_describe_masks_secrets(self) -> None:
        class _SecretfulProviderUpdate(ProviderUpdate):
            @typing.override
            def secret_names(self, for_type: str) -> set[str]:
                return {"name"}

        action = PendingAction.create(
            type_id="provider.update",
            owner_uuid="someone",
            agent="test",
            target_uuid=self.provider.uuid,
            values={"name": "new-secret-value", "comments": "visible"},
            base_values={"name": "old", "comments": "old"},
            base_etag="x",
        )
        described = _SecretfulProviderUpdate().describe(action)
        self.assertEqual(described["changes"]["name"], "(redacted)")
        self.assertEqual(described["changes"]["comments"], "visible")
        self.assertEqual(described["secrets_changed"], ["name"])

    def test_execute_builds_canonical_put(self) -> None:
        action = PendingAction.create(
            type_id="provider.update",
            owner_uuid="someone",
            agent="test",
            target_uuid=self.provider.uuid,
            values={"name": "new-name", "instance": {"host": "h"}},
            base_values={"name": "old", "instance.host": "old"},
            base_etag="x",
        )
        request = mock.MagicMock()
        with (
            mock.patch("uds.mcp.mutability.types_providers.RestProxy") as proxy_cls,
            mock.patch.object(self.action_type, "resolve_target", return_value=self.provider),
        ):
            proxy_cls.return_value.execute = mock.AsyncMock()
            asyncio.run(self.action_type.execute(action, request))
            proxy_cls.return_value.execute.assert_called_once()
            target, called_request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual(
                (target.handler, target.method.value, target.args), (Providers, "PUT", (self.provider.uuid,))
            )
            self.assertEqual(called_request, request)
            self.assertEqual(params, {"name": "new-name", "instance": {"host": "h"}})


class MutabilityToolsRpcTest(rest.test.RESTTestCase):
    """Proposal tools through the MCP JSON-RPC surface."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        GlobalConfig.MCP_ENABLED.set(True)
        GlobalConfig.MCP_MUTATIONS.set(True)
        get_catalog.cache_clear()
        self.token = self.login_with_api_token()
        # Fresh provider: create_db_provider also registers its test module
        # on the factory, so the gui machinery can resolve its data_type.
        self.provider = create_db_provider()
        self.store = PendingActionStore()

    @typing.override
    def tearDown(self) -> None:
        get_catalog.cache_clear()
        super().tearDown()

    def _call(self, name: str, arguments: dict[str, typing.Any]) -> dict[str, typing.Any]:
        response = self.client.rest_post(
            "mcp",
            data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
            ).encode("utf-8"),
        )
        self.assertEqual(response.status_code, 200, response.content)
        return typing.cast("dict[str, typing.Any]", json.loads(response.content))

    def _result_json(self, body: dict[str, typing.Any]) -> dict[str, typing.Any]:
        self.assertNotIn("error", body, body)
        return typing.cast("dict[str, typing.Any]", json.loads(str(body["result"]["content"][0]["text"])))

    def _propose(self, values: dict[str, typing.Any] | None = None) -> dict[str, typing.Any]:
        return self._call(
            "propose_provider_update",
            {
                "target_uuid": self.provider.uuid,
                "values": values if values is not None else {"name": "renamed by agent"},
            },
        )

    def test_mutation_tools_present_when_enabled(self) -> None:
        names = {t.name for t in get_catalog().tools()}
        for name in _MUTATION_TOOL_NAMES:
            self.assertIn(name, names)

    def test_mutation_tools_absent_when_disabled(self) -> None:
        GlobalConfig.MCP_MUTATIONS.set(False)
        get_catalog.cache_clear()
        names = {t.name for t in get_catalog().tools()}
        for name in _MUTATION_TOOL_NAMES:
            self.assertNotIn(name, names)
        GlobalConfig.MCP_MUTATIONS.set(True)
        get_catalog.cache_clear()

    def test_propose_and_list_flow(self) -> None:
        body = self._propose()
        result = self._result_json(body)
        action_id = result["id"]
        self.assertEqual(result["status"], "pending")
        self.assertIn("administrator", result["message"])
        self.assertEqual(result["proposal"]["changes"]["name"], "renamed by agent")

        stored = self.store.get(action_id)
        assert stored is not None
        self.assertEqual(stored.status, ActionStatus.PENDING)
        self.assertEqual(stored.values, {"name": "renamed by agent"})

        listing = self._result_json(self._call("list_pending_actions", {}))
        self.assertEqual(listing["count"], 1)
        item = listing["items"][0]
        self.assertEqual(item["id"], action_id)
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["proposal"]["changes"]["name"], "renamed by agent")

    def test_propose_reports_unknown_fields(self) -> None:
        body = self._propose({"nonexistent_field": 1})
        self.assertIn("error", body)
        self.assertIn("accepted", str(body["error"]))

    def test_update_and_cancel_flow(self) -> None:
        proposal = self._result_json(self._propose({"name": "v1"}))
        action_id = proposal["id"]

        updated = self._result_json(
            self._call("update_pending_action", {"id": action_id, "values": {"name": "v2", "comments": "c"}})
        )
        self.assertEqual(updated["proposal"]["changes"]["name"], "v2")
        stored = self.store.get(action_id)
        assert stored is not None
        self.assertEqual(stored.values, {"name": "v2", "comments": "c"})
        # Re-based CAS: base now matches the untouched current state
        self.assertEqual(stored.base_values, {"name": self.provider.name, "comments": self.provider.comments})

        cancelled = self._result_json(self._call("cancel_pending_action", {"id": action_id}))
        self.assertEqual(cancelled["status"], "cancelled")
        stored = self.store.get(action_id)
        assert stored is not None
        self.assertEqual(stored.status, ActionStatus.CANCELLED)

        # Decided proposals are terminal: no further updates
        body = self._call("update_pending_action", {"id": action_id, "values": {"name": "v3"}})
        self.assertIn("error", body)

    def test_propose_requires_management_permission(self) -> None:
        authenticator = create_db_authenticator()
        staff = create_db_users(authenticator, is_staff=True)[0]

        # Without any permission over the provider: denied
        self.login_with_api_token(user=staff, as_admin=False)
        body = self._propose()
        self.assertIn("error", body)

        # With MANAGEMENT over the provider: accepted
        permissions.add_user_permission(staff, self.provider, types.permissions.PermissionType.MANAGEMENT)
        self.login_with_api_token(user=staff, as_admin=False)
        body = self._propose()
        self.assertNotIn("error", body, body)

    def test_propose_enforces_pending_cap(self) -> None:
        with mock.patch("uds.mcp.mutability.tools.MAX_PENDING_PER_USER", 2):
            self._result_json(self._propose({"name": "one"}))
            self._result_json(self._propose({"name": "two"}))
            body = self._propose({"name": "three"})
        self.assertIn("error", body)
        self.assertIn("limit", str(body["error"]))

    def test_discovery_by_uuid_and_by_type(self) -> None:
        by_uuid = self._result_json(self._call("get_mutable_fields", {"target_uuid": self.provider.uuid}))
        self.assertEqual(by_uuid["target_uuid"], self.provider.uuid)
        names = [d["name"] for d in by_uuid["fields"]]
        self.assertIn("name", names)
        self.assertIn("name", by_uuid["current_values"])

        by_type = self._result_json(self._call("get_mutable_fields", {"for_type": "OpenStackPlatform"}))
        secrets = [d["name"] for d in by_type["fields"] if d["secret"]]
        self.assertIn("instance.password", secrets)

    def test_list_shows_only_own_proposals(self) -> None:
        self._result_json(self._propose({"name": "mine"}))

        authenticator = create_db_authenticator()
        staff = create_db_users(authenticator, is_staff=True)[0]
        permissions.add_user_permission(staff, self.provider, types.permissions.PermissionType.MANAGEMENT)
        self.login_with_api_token(user=staff, as_admin=False)
        listing = self._result_json(self._call("list_pending_actions", {}))
        self.assertEqual(listing["count"], 0)


if __name__ == "__main__":
    unittest.main()
