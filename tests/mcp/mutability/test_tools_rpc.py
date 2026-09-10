"""Proposal tools through the MCP JSON-RPC surface."""

import datetime
import json
import typing
from unittest import mock

from uds.core import types
from uds.core.types.mcp import FlowActionStatus, FlowStatus
from uds.core.util import permissions
from uds.core.util.config import GlobalConfig
from uds.core.util.model import sql_now
from uds.models import ActionFlow
from uds.mcp.default_catalog import get_catalog
from uds.mutability import FlowStore

from tests.fixtures.authenticators import create_db_authenticator, create_db_users
from tests.fixtures.services import create_db_provider
from tests.mcp.mutability._helpers import MUTATION_TOOL_NAMES
from tests.utils import rest

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


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
        self.store = FlowStore()

    @typing.override
    def tearDown(self) -> None:
        get_catalog.cache_clear()
        super().tearDown()

    def _call(self, name: str, arguments: dict[str, typing.Any]) -> dict[str, typing.Any]:
        response = self.client.rest_post(
            "mcp",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                }
            ).encode("utf-8"),
        )
        self.assertEqual(response.status_code, 200, response.content)
        return typing.cast("dict[str, typing.Any]", json.loads(response.content))

    def _result_json(self, body: dict[str, typing.Any]) -> dict[str, typing.Any]:
        self.assertNotIn("error", body, body)
        return typing.cast("dict[str, typing.Any]", json.loads(str(body["result"]["content"][0]["text"])))

    def _propose(
        self, values: dict[str, typing.Any] | None = None, **extra: typing.Any
    ) -> dict[str, typing.Any]:
        return self._call(
            "propose_provider_update",
            {
                "target_uuid": self.provider.uuid,
                "values": values if values is not None else {"name": "renamed by agent"},
                **extra,
            },
        )

    def test_mutation_tools_present_when_enabled(self) -> None:
        names = {t.name for t in get_catalog().tools()}
        for name in MUTATION_TOOL_NAMES:
            self.assertIn(name, names)

    def test_mutation_tools_absent_when_disabled(self) -> None:
        GlobalConfig.MCP_MUTATIONS.set(False)
        get_catalog.cache_clear()
        names = {t.name for t in get_catalog().tools()}
        for name in MUTATION_TOOL_NAMES:
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

        stored = self.store.get_action(action_id)
        assert stored is not None
        self.assertEqual(stored.status, FlowActionStatus.PENDING)
        self.assertEqual(stored.values, {"name": "renamed by agent"})
        self.assertEqual(stored.flow.status, "pending")

        listing = self._result_json(self._call("list_pending_actions", {}))
        self.assertEqual(listing["count"], 1)
        item = listing["items"][0]
        self.assertEqual(item["id"], action_id)
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["flow"]["status"], "pending")
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
        stored = self.store.get_action(action_id)
        assert stored is not None
        self.assertEqual(stored.values, {"name": "v2", "comments": "c"})
        # Re-based CAS: base now matches the untouched current state
        self.assertEqual(stored.base_values, {"name": self.provider.name, "comments": self.provider.comments})

        cancelled = self._result_json(self._call("cancel_pending_action", {"id": action_id}))
        # The decision is flow-level: the flow is cancelled, its pending
        # action is skipped
        self.assertEqual(cancelled["flow_status"], "cancelled")
        self.assertEqual(cancelled["status"], "skipped")
        stored = self.store.get_action(action_id)
        assert stored is not None
        self.assertEqual(stored.status, FlowActionStatus.SKIPPED)
        self.assertEqual(stored.flow.status, "cancelled")

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
        with mock.patch("uds.core.consts.mcp.MAX_FLOWS_PER_USER", 2):
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

    # ------------------------------------------------------- horizon and ttl

    def _expire_flow(self, flow_uuid: str, *, ago: datetime.timedelta) -> None:
        """Force the expiry of a flow (due date in the past + lazy pass)."""
        ActionFlow.objects.filter(uuid=flow_uuid).update(due_date=sql_now() - ago)
        flow = self.store.get_flow(flow_uuid)
        assert flow is not None
        self.assertEqual(flow.status, FlowStatus.EXPIRED)

    def test_propose_honours_expires_in_hours(self) -> None:
        result = self._result_json(self._propose({"name": "quick"}, expires_in_hours=2))
        flow = self.store.get_flow(result["flow_id"])
        assert flow is not None and flow.due_date is not None
        remaining = flow.due_date - sql_now()
        self.assertLess(remaining, datetime.timedelta(hours=3))
        self.assertGreater(remaining, datetime.timedelta(hours=1))

    def test_update_revives_expired_proposal(self) -> None:
        proposal = self._result_json(self._propose({"name": "v1"}))
        action_id, flow_id = proposal["id"], proposal["flow_id"]
        self._expire_flow(flow_id, ago=datetime.timedelta(days=1))

        updated = self._result_json(
            self._call(
                "update_pending_action",
                {"id": action_id, "values": {"name": "v2"}, "expires_in_hours": 5},
            )
        )
        self.assertEqual(updated["status"], "pending")
        flow = self.store.get_flow(flow_id)
        assert flow is not None
        self.assertEqual(flow.status, FlowStatus.PENDING)
        assert flow.due_date is not None
        remaining = flow.due_date - sql_now()
        self.assertLess(remaining, datetime.timedelta(hours=6))
        # Every skipped action came back with the flow
        self.assertEqual(
            list(flow.actions.values_list("status", flat=True)),
            [FlowActionStatus.PENDING],
        )

    def test_update_does_not_revive_old_expired_proposals(self) -> None:
        proposal = self._result_json(self._propose({"name": "v1"}))
        self._expire_flow(proposal["flow_id"], ago=datetime.timedelta(days=30))
        body = self._call(
            "update_pending_action",
            {"id": proposal["id"], "values": {"name": "v2"}},
        )
        self.assertIn("error", body)

    def test_list_respects_visibility_horizon(self) -> None:
        proposal = self._result_json(self._propose({"name": "ancient"}))
        # A flow created (and expired) long ago falls out of the horizon
        ActionFlow.objects.filter(uuid=proposal["flow_id"]).update(
            created=sql_now() - datetime.timedelta(days=40),
            due_date=sql_now() - datetime.timedelta(days=33),
        )
        listing = self._result_json(self._call("list_pending_actions", {}))
        self.assertEqual(listing["count"], 0)

        # An explicit wider window brings it back
        listing = self._result_json(
            self._call(
                "list_pending_actions",
                {"created_after": (sql_now() - datetime.timedelta(days=60)).date().isoformat()},
            )
        )
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["items"][0]["flow"]["status"], FlowStatus.EXPIRED)
