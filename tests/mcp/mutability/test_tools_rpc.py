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
from uds.models import Account, ActionFlow, Calendar
from uds.mcp.default_catalog import get_catalog
from uds.mutability import FlowStore

from tests.fixtures.authenticators import create_db_authenticator, create_db_groups, create_db_users
from tests.fixtures.services import (
    create_db_osmanager,
    create_db_provider,
    create_db_service,
    create_db_servicepool,
)
from tests.mcp.mutability._helpers import MUTATION_TOOL_NAMES, READ_TOOL_NAMES
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

    def _create_flow(self, **extra: typing.Any) -> dict[str, typing.Any]:
        return self._result_json(self._call("create_flow", {"name": "rpc flow", **extra}))

    def _propose(
        self, flow_id: str, values: dict[str, typing.Any] | None = None, **extra: typing.Any
    ) -> dict[str, typing.Any]:
        return self._result_json(
            self._call(
                "propose_provider_update",
                {
                    "flow_id": flow_id,
                    "target_uuid": self.provider.uuid,
                    "values": values if values is not None else {"name": "renamed by agent"},
                    **extra,
                },
            )
        )

    def test_mutation_tools_present_when_enabled(self) -> None:
        names = {t.name for t in get_catalog().tools()}
        for name in MUTATION_TOOL_NAMES:
            self.assertIn(name, names)
        for name in READ_TOOL_NAMES:
            self.assertIn(name, names)

    def test_mutation_tools_absent_when_disabled(self) -> None:
        GlobalConfig.MCP_MUTATIONS.set(False)
        get_catalog.cache_clear()
        names = {t.name for t in get_catalog().tools()}
        for name in MUTATION_TOOL_NAMES:
            self.assertNotIn(name, names)
        # Reads are not mutations: the generated descriptor read tools stay.
        for name in READ_TOOL_NAMES:
            self.assertIn(name, names)
        GlobalConfig.MCP_MUTATIONS.set(True)
        get_catalog.cache_clear()

    def test_flow_lifecycle(self) -> None:
        draft = self._create_flow()
        self.assertEqual(draft["status"], FlowStatus.DRAFT)
        flow_id = draft["flow_id"]

        result = self._propose(flow_id)
        action_id = result["id"]
        self.assertEqual(result["status"], "pending")
        self.assertIn("administrator", result["message"])
        self.assertEqual(result["proposal"]["changes"]["name"], "renamed by agent")

        stored = self.store.get_action(action_id)
        assert stored is not None
        self.assertEqual(stored.status, FlowActionStatus.PENDING)
        self.assertEqual(stored.values, {"name": "renamed by agent"})
        self.assertEqual(stored.flow.status, FlowStatus.DRAFT)

        # A draft flow is invisible to the administration surface but the
        # owner lists it with its actions
        listing = self._result_json(self._call("list_flow_actions", {}))
        self.assertEqual(listing["count"], 1)
        item = listing["items"][0]
        self.assertEqual(item["id"], action_id)
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["flow"]["status"], FlowStatus.DRAFT)
        self.assertEqual(item["proposal"]["changes"]["name"], "renamed by agent")

        # Submit: the whole flow becomes the administrator's to review
        submitted = self._result_json(self._call("submit_flow", {"flow_id": flow_id}))
        self.assertEqual(submitted["status"], FlowStatus.PENDING)
        flow = self.store.get_flow(flow_id)
        assert flow is not None
        self.assertEqual(flow.status, FlowStatus.PENDING)

        # Submitted flows are final: no further actions, no edits
        body = self._call(
            "propose_provider_update",
            {"flow_id": flow_id, "target_uuid": self.provider.uuid, "values": {"name": "late"}},
        )
        self.assertIn("error", body)
        body = self._call("update_flow_action", {"id": action_id, "values": {"name": "late"}})
        self.assertIn("error", body)

    def test_propose_requires_flow_id(self) -> None:
        body = self._call(
            "propose_provider_update",
            {"target_uuid": self.provider.uuid, "values": {"name": "orphan"}},
        )
        self.assertIn("error", body)
        self.assertEqual(ActionFlow.objects.count(), 0)

    def test_propose_stores_justification(self) -> None:
        # The schema accepts it and it lands on the action: it is what the
        # administrator reads to decide.
        tool = next(t for t in get_catalog().tools() if t.name == "propose_provider_update")
        self.assertIn("justification", (tool.input_schema or {}).get("properties", {}))

        flow_id = self._create_flow(justification="ticket #1234")["flow_id"]
        result = self._propose(flow_id, {"name": "renamed"}, justification="ticket #1234")
        flow = self.store.get_flow(flow_id)
        assert flow is not None
        action = self.store.get_action(result["id"])
        assert action is not None
        self.assertEqual(flow.justification, "ticket #1234")
        self.assertEqual(action.justification, "ticket #1234")

    def test_propose_reports_unknown_fields(self) -> None:
        flow_id = self._create_flow()["flow_id"]
        body = self._call(
            "propose_provider_update",
            {"flow_id": flow_id, "target_uuid": self.provider.uuid, "values": {"nonexistent_field": 1}},
        )
        self.assertIn("error", body)
        self.assertIn("accepted", str(body["error"]))
        # The rejected action leaves the draft flow alive for a retry
        self.assertEqual(ActionFlow.objects.count(), 1)

    def test_delete_tools_do_not_require_values(self) -> None:
        # The schema itself: flow_id + target_uuid, values not demanded
        tool = next(t for t in get_catalog().tools() if t.name == "propose_account_delete")
        schema = tool.input_schema or {}
        self.assertNotIn("values", schema.get("required", []))
        self.assertIn("target_uuid", schema.get("required", []))

        account = Account.objects.create(name="rpc account")
        flow_id = self._create_flow()["flow_id"]
        # An omitted values object is the normal shape of a deletion
        result = self._result_json(
            self._call("propose_account_delete", {"flow_id": flow_id, "target_uuid": account.uuid})
        )
        self.assertEqual(result["status"], "pending")
        stored = self.store.get_action(result["id"])
        assert stored is not None
        self.assertEqual(stored.action_type, "account.delete")
        self.assertEqual(stored.values, {})

        # And an explicit empty dict works the same
        calendar = Calendar.objects.create(name="rpc calendar")
        result = self._result_json(
            self._call(
                "propose_calendar_delete",
                {"flow_id": flow_id, "target_uuid": calendar.uuid, "values": {}},
            )
        )
        stored = self.store.get_action(result["id"])
        assert stored is not None
        self.assertEqual(stored.values, {})

    def test_non_delete_verbs_still_require_values(self) -> None:
        calendar = Calendar.objects.create(name="rpc calendar")
        flow_id = self._create_flow()["flow_id"]
        body = self._call("propose_calendar_update", {"flow_id": flow_id, "target_uuid": calendar.uuid})
        self.assertIn("error", body)
        self.assertIn("values", str(body["error"]))

    def test_rule_creation_discovery_targets_the_parent_calendar(self) -> None:
        # The registry fix: a detail creation resolves the PARENT uuid
        # through the container hook, not resolve_target (the rule does
        # not exist yet)
        calendar = Calendar.objects.create(name="rpc calendar")
        result = self._result_json(
            self._call(
                "get_mutable_fields",
                {"action_type": "calendar_rule.create", "target_uuid": calendar.uuid},
            )
        )
        self.assertEqual(result["target_uuid"], calendar.uuid)
        names = [d["name"] for d in result["fields"]]
        self.assertEqual(
            names,
            ["name", "comments", "frequency", "start", "end", "interval", "duration", "duration_unit"],
        )

    def test_update_and_cancel_flow(self) -> None:
        flow_id = self._create_flow()["flow_id"]
        proposal = self._propose(flow_id, {"name": "v1"})
        action_id = proposal["id"]

        updated = self._result_json(
            self._call("update_flow_action", {"id": action_id, "values": {"name": "v2", "comments": "c"}})
        )
        self.assertEqual(updated["proposal"]["changes"]["name"], "v2")
        stored = self.store.get_action(action_id)
        assert stored is not None
        self.assertEqual(stored.values, {"name": "v2", "comments": "c"})
        # Re-based CAS: base now matches the untouched current state
        self.assertEqual(stored.base_values, {"name": self.provider.name, "comments": self.provider.comments})

        cancelled = self._result_json(self._call("cancel_flow", {"flow_id": flow_id}))
        # The decision is flow-level: the flow is cancelled, its pending
        # action is skipped
        self.assertEqual(cancelled["flow_status"], "cancelled")
        stored = self.store.get_action(action_id)
        assert stored is not None
        self.assertEqual(stored.status, FlowActionStatus.SKIPPED)
        self.assertEqual(stored.flow.status, "cancelled")

        # Decided flows are terminal: no further updates
        body = self._call("update_flow_action", {"id": action_id, "values": {"name": "v3"}})
        self.assertIn("error", body)

    def test_cancel_only_targets_the_whole_flow(self) -> None:
        flow_id = self._create_flow()["flow_id"]
        self._propose(flow_id)
        # An action id is not a flow id: cancelling needs the flow
        body = self._call("cancel_flow", {"flow_id": "not-a-flow"})
        self.assertIn("error", body)
        self.assertEqual(ActionFlow.objects.get(uuid=flow_id).status, FlowStatus.DRAFT)

    def test_propose_requires_management_permission(self) -> None:
        authenticator = create_db_authenticator()
        staff = create_db_users(authenticator, is_staff=True)[0]

        self.login_with_api_token(user=staff, as_admin=False)
        flow_id = self._create_flow()["flow_id"]

        # Without any permission over the provider: denied
        body = self._call(
            "propose_provider_update",
            {"flow_id": flow_id, "target_uuid": self.provider.uuid, "values": {"name": "x"}},
        )
        self.assertIn("error", body)

        # With MANAGEMENT over the provider: accepted
        permissions.add_user_permission(staff, self.provider, types.permissions.PermissionType.MANAGEMENT)
        self.login_with_api_token(user=staff, as_admin=False)
        result = self._propose(flow_id)
        self.assertIn("id", result)

    def test_create_flow_enforces_open_cap(self) -> None:
        limit = mock.patch.object(
            GlobalConfig, "MCP_MAX_FLOWS_PER_USER", mock.Mock(as_int=mock.Mock(return_value=2))
        )
        with limit:
            self._create_flow(name="one")
            self._create_flow(name="two")
            body = self._call("create_flow", {"name": "three"})
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

    def test_discovery_create_without_gallery_defaults_for_type(self) -> None:
        # Pool families (and network/tunnel) have no subtype gallery: the
        # creation surface is reached without declaring for_type.
        for action_type, root in (("servicepool.create", "servicepool"), ("metapool.create", "metapool")):
            with self.subTest(action_type=action_type):
                result = self._result_json(self._call("get_mutable_fields", {"action_type": action_type}))
                self.assertEqual(result["action_type"], action_type)
                self.assertEqual(result["for_type"], root)
                names = [d["name"] for d in result["fields"]]
                self.assertIn("name", names)
                self.assertIn("tags", names)
        servicepool_fields = self._result_json(
            self._call("get_mutable_fields", {"action_type": "servicepool.create"})
        )["fields"]
        # The creation view selects references instead of hiding them
        self.assertIn("service_id", [d["name"] for d in servicepool_fields])

    def test_discovery_create_with_gallery_demands_for_type(self) -> None:
        # Module families still require the subtype: the error points to
        # the gallery tool instead of guessing a root for_type.
        body = self._call("get_mutable_fields", {"action_type": "provider.create"})
        self.assertIn("error", body)
        self.assertIn("get_creatable_types", str(body["error"]))

    def test_relation_read_tool_returns_current_members(self) -> None:
        service = create_db_service(self.provider)
        pool = create_db_servicepool(service=service, osmanager=create_db_osmanager())
        group = create_db_groups(create_db_authenticator(), 1)[0]
        pool.assignedGroups.add(group)
        view = self._result_json(self._call("get_servicepool_group", {"target_uuid": pool.uuid}))
        self.assertEqual(view["entity_type"], "servicepool.group")
        self.assertEqual(view["current_values"], {"groups": [group.uuid]})
        self.assertEqual(view["current_members"], [{"uuid": group.uuid, "name": group.name}])

    def test_relation_read_tool_refuses_unknown_target(self) -> None:
        # The generated get_* tools answer directly (no flow): resolve or NotFound
        body = self._call("get_servicepool_group", {"target_uuid": "00000000-0000-0000-0000-000000000000"})
        self.assertIn("error", body)
        self.assertIn("not found", str(body["error"]).lower())

    def test_relation_read_tool_requires_target_uuid(self) -> None:
        body = self._call("get_metapool_member", {})
        self.assertIn("error", body)
        self.assertIn("target_uuid", str(body["error"]))

    def test_list_shows_only_own_proposals(self) -> None:
        self._propose(self._create_flow()["flow_id"], {"name": "mine"})

        authenticator = create_db_authenticator()
        staff = create_db_users(authenticator, is_staff=True)[0]
        permissions.add_user_permission(staff, self.provider, types.permissions.PermissionType.MANAGEMENT)
        self.login_with_api_token(user=staff, as_admin=False)
        listing = self._result_json(self._call("list_flow_actions", {}))
        self.assertEqual(listing["count"], 0)

    # ------------------------------------------------------- horizon and ttl

    def test_submit_honours_expires_in_hours(self) -> None:
        flow_id = self._create_flow()["flow_id"]
        self._propose(flow_id, {"name": "quick"})
        self._result_json(self._call("submit_flow", {"flow_id": flow_id, "expires_in_hours": 2}))
        flow = self.store.get_flow(flow_id)
        assert flow is not None and flow.due_date is not None
        remaining = flow.due_date - sql_now()
        self.assertLess(remaining, datetime.timedelta(hours=3))
        self.assertGreater(remaining, datetime.timedelta(hours=1))

    def test_expiry_is_terminal(self) -> None:
        flow_id = self._create_flow()["flow_id"]
        action_id = self._propose(flow_id, {"name": "v1"})["id"]
        self._result_json(self._call("submit_flow", {"flow_id": flow_id}))
        # Force the expiry of the submitted flow
        ActionFlow.objects.filter(uuid=flow_id).update(due_date=sql_now() - datetime.timedelta(days=1))
        flow = self.store.get_flow(flow_id)
        assert flow is not None
        self.assertEqual(flow.status, FlowStatus.EXPIRED)

        # Expired flows never come back: the update refuses (draft only)
        body = self._call("update_flow_action", {"id": action_id, "values": {"name": "v2"}})
        self.assertIn("error", body)
        flow = self.store.get_flow(flow_id)
        assert flow is not None
        self.assertEqual(flow.status, FlowStatus.EXPIRED)

    def test_list_respects_visibility_horizon(self) -> None:
        flow_id = self._create_flow()["flow_id"]
        self._propose(flow_id, {"name": "ancient"})
        # A flow created (and expired) long ago falls out of the horizon
        ActionFlow.objects.filter(uuid=flow_id).update(
            created=sql_now() - datetime.timedelta(days=40),
            due_date=sql_now() - datetime.timedelta(days=33),
        )
        self.store.get_flow(flow_id)  # lazy expiry pass
        listing = self._result_json(self._call("list_flow_actions", {}))
        self.assertEqual(listing["count"], 0)

        # An explicit wider window brings it back
        listing = self._result_json(
            self._call(
                "list_flow_actions",
                {"created_after": (sql_now() - datetime.timedelta(days=60)).date().isoformat()},
            )
        )
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["items"][0]["flow"]["status"], FlowStatus.EXPIRED)
