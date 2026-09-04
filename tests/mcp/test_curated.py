"""Tests for the hand-curated MCP tools (``uds.mcp.curated``)."""

import json
import typing
import unittest

from django.utils import timezone

from uds import models
from uds.core.types.log import LogObjectType
from uds.core.util.config import GlobalConfig
from uds.core.util.log import LogLevel, LogSource, log
from uds.mcp import get_catalog
from uds.models.log import Log

from tests.utils import rest

_CURATED_NAMES: typing.Final[tuple[str, ...]] = (
    "get_servicepool_fallback_access",
    "get_metapool_fallback_access",
    "get_servicepool_forecast",
    "get_servicepool_cache_recommendations",
    "get_server_group_stats",
    "search_authenticator",
    "get_server_stats",
    "get_item_logs",
    "get_system_logs",
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

    def test_search_authenticator(self) -> None:
        body = self._call(
            "search_authenticator",
            {"uuid": self.auth.uuid, "type": "user", "term": self.plain_users[0].name[:4]},
        )
        self.assertIsInstance(json.loads(self._result_text(body)), list)

    def test_search_authenticator_requires_type_and_term(self) -> None:
        body = self._call("search_authenticator", {"uuid": self.auth.uuid})
        self.assertEqual(body["error"]["code"], -32602)
