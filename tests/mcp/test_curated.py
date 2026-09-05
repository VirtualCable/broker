"""Tests for the hand-curated MCP tools (``uds.mcp.curated``)."""

import inspect
import json
import re
import typing
import unittest
from unittest import mock

from django.utils import timezone

from uds import models
from uds.core.types.log import LogObjectType
from uds.core.util.config import GlobalConfig
from uds.core.util.log import LogLevel, LogSource, log
from uds.mcp import get_catalog
from uds.mcp.curated import curated_tools
from uds.models.log import Log
from uds.REST.methods.reports import Reports

from tests.utils import rest

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false

_CURATED_NAMES: typing.Final[tuple[str, ...]] = (
    "get_servicepool_fallback_access",
    "get_metapool_fallback_access",
    "get_servicepool_forecast",
    "get_servicepool_cache_recommendations",
    "get_servicepool_actions_list",
    "get_servicepool_assignables",
    "get_server_group_stats",
    "search_authenticator",
    "get_authenticator_users_with_services",
    "get_authenticator_user_services_pools",
    "get_authenticator_user_user_services",
    "get_authenticator_group_services_pools",
    "get_authenticator_group_users",
    "get_provider_allservices",
    "get_provider_service",
    "get_provider_service_servicepools",
    "get_tunnel_group_unassigned_tunnels",
    "get_server_stats",
    "get_item_logs",
    "get_system_logs",
    "get_platform_stats",
    "get_security_check",
    "report_failed_logins",
    "report_admin_activity",
)

_MARKER: typing.Final[str] = "mcp-system-log-marker-for-test"


class CuratedCatalogTest(unittest.TestCase):
    """The curated tools are part of the default catalog."""

    def test_curated_tools_are_registered(self) -> None:
        names = {tool.name for tool in get_catalog().tools()}
        for name in _CURATED_NAMES:
            self.assertIn(name, names)


class CuratedToolsJsonRpcTest(rest.test.RESTTestCase):
    """Curated tools answer through the MCP JSON-RPC surface."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        GlobalConfig.MCP_ENABLED.set(True)
        log(None, LogLevel.INFO, _MARKER, source=LogSource.INTERNAL)
        self.token = self.login_with_api_token()

    def _call(self, name: str, arguments: dict[str, typing.Any]) -> dict[str, typing.Any]:
        response = self.client.rest_post(
            "mcp",
            data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
            ).encode("utf-8"),
        )
        self.assertEqual(response.status_code, 200, response.content)
        return typing.cast("dict[str, typing.Any]", json.loads(response.content))

    def _result_text(self, body: dict[str, typing.Any]) -> str:
        self.assertNotIn("error", body, body)
        return str(body["result"]["content"][0]["text"])

    def _a_service_pool(self) -> models.ServicePool:
        pool = models.ServicePool.objects.first()
        assert pool is not None, "RESTTestCase fixtures must create at least one service pool"
        return pool

    def test_get_system_logs_as_admin(self) -> None:
        body = self._call("get_system_logs", {})
        self.assertIn(_MARKER, self._result_text(body))

    def test_get_system_logs_reports_truncation(self) -> None:
        # Guarantee at least two entries exist: on a fresh database the
        # request itself is only logged after it is answered.
        log(None, LogLevel.INFO, f"{_MARKER}-second", source=LogSource.INTERNAL)
        body = self._call("get_system_logs", {"limit": 1})
        text = self._result_text(body)
        self.assertIn('"truncated": true', text)
        self.assertIn("hint", text)

    def test_get_system_logs_passes_odata_filter(self) -> None:
        # Seed a newer non-internal entry: without the $filter it would win
        # the first page, so seeing the internal one proves the filter ran
        # at the queryset level.
        Log.objects.create(
            owner_type=LogObjectType.SYSLOG.value,
            owner_id=-1,
            created=timezone.now(),
            source="rest",
            level=LogLevel.INFO.value,
            data="mcp-system-log-rest-marker",
            name="",
        )
        body = self._call("get_system_logs", {"filter": "source eq 'internal'", "limit": 1})
        result = typing.cast("dict[str, typing.Any]", json.loads(self._result_text(body)))
        entries = typing.cast("list[dict[str, typing.Any]]", result["entries"])
        self.assertTrue(entries)
        self.assertEqual(str(entries[0]["source"]).lower(), "internal")

        # Control: without the filter the newest entry is the REST one.
        body = self._call("get_system_logs", {"limit": 1})
        result = typing.cast("dict[str, typing.Any]", json.loads(self._result_text(body)))
        entries = typing.cast("list[dict[str, typing.Any]]", result["entries"])
        self.assertEqual(str(entries[0]["source"]).lower(), "rest")

    def test_get_platform_stats_global_complete(self) -> None:
        body = self._call("get_platform_stats", {"counter": "complete"})
        result = json.loads(self._result_text(body))
        for series in ("assigned", "inuse", "cached"):
            self.assertIn(series, result)

    def test_get_platform_stats_for_one_pool(self) -> None:
        pool = self._a_service_pool()
        body = self._call("get_platform_stats", {"counter": "assigned", "pool_uuid": pool.uuid})
        self.assertIsInstance(json.loads(self._result_text(body)), list)

    def test_get_platform_stats_rejects_unknown_counter(self) -> None:
        body = self._call("get_platform_stats", {"counter": "nope"})
        self.assertEqual(body["error"]["code"], -32602)

    def test_get_platform_stats_global_is_denied_for_staff(self) -> None:
        self.login_with_api_token(as_admin=False)
        body = self._call("get_platform_stats", {"counter": "assigned"})
        self.assertEqual(body["error"]["code"], -32000)

    def test_get_security_check_as_admin(self) -> None:
        body = self._call("get_security_check", {})
        self.assertIsInstance(json.loads(self._result_text(body)), dict)

    def test_report_failed_logins_csv(self) -> None:
        body = self._call(
            "report_failed_logins",
            {"start_date": "2026-01-01", "end_date": "2026-12-31"},
        )
        result = typing.cast("dict[str, typing.Any]", json.loads(self._result_text(body)))
        self.assertEqual(result["mime_type"], "text/csv")
        self.assertFalse(result["truncated"])
        self.assertIsInstance(result["data"], str)

    def test_report_admin_activity_csv(self) -> None:
        body = self._call(
            "report_admin_activity",
            {"start_date": "2026-01-01", "end_date": "2026-12-31", "top_paths": 10},
        )
        result = typing.cast("dict[str, typing.Any]", json.loads(self._result_text(body)))
        self.assertEqual(result["mime_type"], "text/csv")
        self.assertFalse(result["truncated"])

    def test_report_output_is_size_capped_with_hint(self) -> None:
        class _HugeReport:
            mime_type = "text/csv"
            encoded = False
            filename = "huge.csv"

            def generate_encoded(self) -> str:
                return "x" * 70000

        with mock.patch.object(Reports, "_locate_report", return_value=_HugeReport()):
            body = self._call(
                "report_failed_logins",
                {"start_date": "2026-01-01", "end_date": "2026-12-31"},
            )
        result = typing.cast("dict[str, typing.Any]", json.loads(self._result_text(body)))
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["data"]), 65536)
        self.assertIn("Narrow the date range", result["hint"])

    def test_get_system_logs_is_denied_for_staff(self) -> None:
        self.login_with_api_token(as_admin=False)
        body = self._call("get_system_logs", {})
        self.assertEqual(body["error"]["code"], -32000)

    def test_get_item_logs_for_a_service_pool(self) -> None:
        pool = self._a_service_pool()
        body = self._call("get_item_logs", {"collection": "service_pool", "uuid": pool.uuid})
        self.assertIsInstance(json.loads(self._result_text(body)), list)

    def test_get_item_logs_requires_item_id_for_detail_collections(self) -> None:
        body = self._call("get_item_logs", {"collection": "user", "uuid": self.auth.uuid})
        self.assertEqual(body["error"]["code"], -32602)

    def test_get_item_logs_rejects_unknown_collection(self) -> None:
        body = self._call("get_item_logs", {"collection": "nope", "uuid": self.auth.uuid})
        self.assertEqual(body["error"]["code"], -32602)

    def test_get_servicepool_fallback_access(self) -> None:
        pool = self._a_service_pool()
        body = self._call("get_servicepool_fallback_access", {"uuid": pool.uuid})
        # The REST custom method answers the policy name ("ALLOW", ...)
        self.assertIsInstance(json.loads(self._result_text(body)), str)

    def test_get_servicepool_actions_list(self) -> None:
        pool = self._a_service_pool()
        body = self._call("get_servicepool_actions_list", {"uuid": pool.uuid})
        actions = json.loads(self._result_text(body))
        self.assertIsInstance(actions, list)
        self.assertTrue(actions, "expected at least one calendar action")
        for action in actions:
            # CalendarAction is a TypedDict (id, description, params).
            self.assertIsInstance(action, dict)
            self.assertIn("id", action)
            self.assertIn("description", action)
            self.assertIn("params", action)
            self.assertIsInstance(action["id"], str)

    def test_get_servicepool_assignables(self) -> None:
        pool = self._a_service_pool()
        body = self._call("get_servicepool_assignables", {"uuid": pool.uuid})
        # Empty list is acceptable (newly created pools may have no
        # assignables yet); we only assert the call returns cleanly.
        self.assertIsInstance(json.loads(self._result_text(body)), list)

    def test_search_authenticator(self) -> None:
        body = self._call(
            "search_authenticator",
            {"uuid": self.auth.uuid, "type": "user", "term": self.plain_users[0].name[:4]},
        )
        self.assertIsInstance(json.loads(self._result_text(body)), list)

    def test_search_authenticator_requires_type_and_term(self) -> None:
        body = self._call("search_authenticator", {"uuid": self.auth.uuid})
        self.assertEqual(body["error"]["code"], -32602)

    def test_get_authenticator_users_with_services(self) -> None:
        body = self._call(
            "get_authenticator_users_with_services",
            {"uuid": self.auth.uuid},
        )
        # Smoke-check the call returns a list; an empty list is acceptable
        # when the fixtures have no assigned services yet.
        self.assertIsInstance(json.loads(self._result_text(body)), list)

    def test_get_authenticator_user_services_pools(self) -> None:
        user = self.users[0]
        body = self._call(
            "get_authenticator_user_services_pools",
            {"uuid": self.auth.uuid, "item_id": user.uuid},
        )
        self.assertIsInstance(json.loads(self._result_text(body)), list)

    def test_get_authenticator_user_user_services(self) -> None:
        user = self.users[0]
        body = self._call(
            "get_authenticator_user_user_services",
            {"uuid": self.auth.uuid, "item_id": user.uuid},
        )
        self.assertIsInstance(json.loads(self._result_text(body)), list)

    def test_get_authenticator_group_services_pools(self) -> None:
        group = self.simple_groups[0]
        body = self._call(
            "get_authenticator_group_services_pools",
            {"uuid": self.auth.uuid, "item_id": group.uuid},
        )
        self.assertIsInstance(json.loads(self._result_text(body)), list)

    def test_get_authenticator_group_users(self) -> None:
        group = self.simple_groups[0]
        body = self._call(
            "get_authenticator_group_users",
            {"uuid": self.auth.uuid, "item_id": group.uuid},
        )
        self.assertIsInstance(json.loads(self._result_text(body)), list)

    def test_get_authenticator_user_user_services_rejects_missing_item_id(self) -> None:
        body = self._call(
            "get_authenticator_user_user_services",
            {"uuid": self.auth.uuid},
        )
        self.assertEqual(body["error"]["code"], -32602)

    def test_get_provider_allservices(self) -> None:
        # ``path_args=()`` → the URL is ``providers/allservices`` with no uuid.
        body = self._call("get_provider_allservices", {})
        self.assertIsInstance(json.loads(self._result_text(body)), list)

    def test_get_provider_service(self) -> None:
        service = self.provider.services.first()
        assert service is not None, "Test fixtures must seed at least one service on the provider"
        # ``path_args=("item_id",)`` → the URL is ``providers/service/{uuid}``.
        body = self._call("get_provider_service", {"item_id": service.uuid})
        payload = json.loads(self._result_text(body))
        # The REST handler answers a BaseRestItem; at minimum the id round-trips.
        self.assertEqual(payload.get("id"), service.uuid)

    def test_get_provider_service_servicepools(self) -> None:
        service = self.provider.services.first()
        assert service is not None
        body = self._call(
            "get_provider_service_servicepools",
            {"uuid": self.provider.uuid, "item_id": service.uuid},
        )
        self.assertIsInstance(json.loads(self._result_text(body)), list)

    def test_get_tunnel_group_unassigned_tunnels(self) -> None:
        from uds.core import types as core_types
        from tests.fixtures.servers import create_server_group

        group = create_server_group(type=core_types.servers.ServerType.TUNNEL, num_servers=2)
        body = self._call(
            "get_tunnel_group_unassigned_tunnels",
            {"uuid": group.uuid},
        )
        self.assertIsInstance(json.loads(self._result_text(body)), list)

    def test_get_metapool_fallback_access(self) -> None:
        from tests.fixtures.services import create_db_metapool

        metapool = create_db_metapool([self._a_service_pool()], self.groups)
        body = self._call("get_metapool_fallback_access", {"uuid": metapool.uuid})
        # Same contract as the service pool variant: the policy name.
        self.assertIsInstance(json.loads(self._result_text(body)), str)

    def test_get_servicepool_forecast(self) -> None:
        pool = self._a_service_pool()
        body = self._call("get_servicepool_forecast", {"uuid": pool.uuid})
        result = json.loads(self._result_text(body))
        # Fresh pools have no samples: the contract still holds (empty forecast).
        self.assertEqual(result["counter"], "inuse")
        self.assertFalse(result["has_data"])
        self.assertIsInstance(result["points"], list)

    def test_get_servicepool_forecast_accepts_counter_and_hours(self) -> None:
        pool = self._a_service_pool()
        body = self._call("get_servicepool_forecast", {"uuid": pool.uuid, "counter": "cached", "hours": 24})
        result = json.loads(self._result_text(body))
        self.assertEqual(result["counter"], "cached")
        self.assertLessEqual(len(result["points"]), 24)

    def test_get_servicepool_cache_recommendations(self) -> None:
        pool = self._a_service_pool()
        body = self._call("get_servicepool_cache_recommendations", {"uuid": pool.uuid})
        result = json.loads(self._result_text(body))
        self.assertIn("current_config", result)
        self.assertEqual(
            set(result["current_config"]),
            {"initial_srvs", "cache_l1_srvs", "cache_l2_srvs", "max_srvs"},
        )
        self.assertIsInstance(result["slots"], list)

    def test_get_server_group_stats(self) -> None:
        from tests.fixtures.servers import create_server_group

        group = create_server_group(num_servers=2)
        body = self._call("get_server_group_stats", {"uuid": group.uuid})
        stats = json.loads(self._result_text(body))
        self.assertIsInstance(stats, list)
        self.assertEqual(len(stats), 2)
        for entry in stats:
            self.assertIn("server", entry)
            self.assertIn("stats", entry)

    def test_get_server_stats(self) -> None:
        from tests.fixtures.servers import create_server_group

        group = create_server_group(num_servers=1)
        server = group.servers.first()
        assert server is not None, "the fixture must attach one server to the group"
        body = self._call(
            "get_server_stats",
            {"group_uuid": group.uuid, "server_uuid": server.uuid},
        )
        result = json.loads(self._result_text(body))
        # counter defaults to "all": one points list per known counter.
        self.assertIsInstance(result, dict)
        self.assertEqual(set(result), {"cpu", "memory", "users", "connections", "disk"})

    def test_get_server_stats_single_counter(self) -> None:
        from tests.fixtures.servers import create_server_group

        group = create_server_group(num_servers=1)
        server = group.servers.first()
        assert server is not None
        body = self._call(
            "get_server_stats",
            {"group_uuid": group.uuid, "server_uuid": server.uuid, "counter": "cpu"},
        )
        # Single counter: the bare points list, not the per-counter mapping.
        self.assertIsInstance(json.loads(self._result_text(body)), list)


class CuratedContractTest(unittest.TestCase):
    """Keep the curated surface wired to its registration list and its tests.

    These guards make the contract explicit: adding (or removing) a curated
    tool without updating ``_CURATED_NAMES`` or without a functional test
    that actually exercises it turns the suite red immediately.
    """

    def test_curated_names_list_matches_curated_tools(self) -> None:
        self.assertEqual(set(_CURATED_NAMES), {tool.name for tool in curated_tools()})

    def test_every_curated_tool_is_functionally_exercised(self) -> None:
        source = inspect.getsource(CuratedToolsJsonRpcTest)
        called: set[str] = set(re.findall(r'self\._call\(\s*"([a-z_0-9]+)"', source))
        missing = {tool.name for tool in curated_tools()} - called
        self.assertFalse(missing, f"curated tools without a functional test: {sorted(missing)}")
