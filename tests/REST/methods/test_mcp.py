"""Functional tests for the JSON-RPC endpoint exposed at ``/uds/rest/mcp``.

The tests pin the JSON-RPC contract (envelope, error codes, version) so
the MCP surface does not regress them.
"""

import json
import typing
from unittest import mock

from uds.core.util.config import GlobalConfig
from uds.mcp import redact

from tests.utils import rest


_JsonRpcObject = dict[str, typing.Any]


class MCPRPCTest(rest.test.RESTTestCase):
    """Pin the JSON-RPC envelope of the MCP endpoint."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        GlobalConfig.MCP_ENABLED.set(True)
        self.login_with_api_token()

    def _post_jsonrpc(self, body: dict[str, object]) -> _JsonRpcObject:
        response = self.client.rest_post("mcp", data=json.dumps(body).encode("utf-8"))
        self.assertEqual(response.status_code, 200, response.content)
        return typing.cast(_JsonRpcObject, json.loads(response.content))

    def test_endpoint_denies_untrusted_sources(self) -> None:
        """MCP inherits the admin trusted-host policy."""
        GlobalConfig.ADMIN_TRUSTED_SOURCES.set("10.0.0.0/8")
        response = self.client.rest_post(
            "mcp", data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode("utf-8")
        )
        self.assertEqual(response.status_code, 403, response.content)

    def test_endpoint_disabled_by_default(self) -> None:
        """Without the config gate enabled the endpoint does not exist."""
        GlobalConfig.MCP_ENABLED.set(False)
        response = self.client.rest_post(
            "mcp", data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode("utf-8")
        )
        self.assertEqual(response.status_code, 404, response.content)

    def test_rate_limit_returns_jsonrpc_error(self) -> None:
        """Requests beyond the per-user limit get a rate-limit JSON-RPC error."""
        GlobalConfig.MCP_RATE_LIMIT.set("2")
        for request_id in (1, 2):
            response = self._post_jsonrpc({"jsonrpc": "2.0", "id": request_id, "method": "ping"})
            self.assertNotIn("error", response)
        third = self._post_jsonrpc({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        self.assertIn("error", third)
        self.assertEqual(third["id"], 3)
        self.assertEqual(third["error"]["code"], -32000)
        self.assertIn("Too many MCP requests", third["error"]["message"])

    def test_tool_calls_are_audited(self) -> None:
        """Successful and failed tool calls are recorded in the audit log."""
        with mock.patch("uds.REST.methods.mcp.MCP._audit") as audit:
            ok = self._post_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": 40,
                    "method": "tools/call",
                    "params": {"name": "list_authenticators", "arguments": {}},
                }
            )
            self.assertNotIn("error", ok)
            failing = self._post_jsonrpc(
                {
                    "jsonrpc": "2.0",
                    "id": 41,
                    "method": "tools/call",
                    "params": {"name": "list_nonexistent", "arguments": {}},
                }
            )
            self.assertIn("error", failing)

        calls = [typing.cast(str, args[0]) for args, _kwargs in audit.call_args_list]
        self.assertTrue(any(c.startswith("tools/call list_authenticators") for c in calls))
        self.assertTrue(any(c.startswith("tools/call list_nonexistent") for c in calls))
        outcomes = [args[1] for args, _kwargs in audit.call_args_list]
        self.assertIn("ok", outcomes)
        self.assertIn("error -32602", outcomes)

    def test_initialize_returns_protocol_version_and_capabilities(self) -> None:
        """``initialize`` returns the protocol version and empty capabilities."""
        response = self._post_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2026-07-28", "capabilities": {}},
            }
        )

        self.assertEqual(response["jsonrpc"], "2.0")
        self.assertEqual(response["id"], 1)
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2026-07-28")
        self.assertEqual(result["serverInfo"]["name"], "UDS")
        self.assertEqual(result["capabilities"], {"tools": {}, "resources": {}})
        self.assertIn("instructions", result)

    def test_initialize_negotiates_supported_version(self) -> None:
        """A supported client version is echoed back verbatim."""
        response = self._post_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
            }
        )
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")

    def test_initialize_falls_back_to_latest_version(self) -> None:
        """An unknown client version gets our latest supported revision."""
        response = self._post_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "initialize",
                "params": {"protocolVersion": "2000-01-01", "capabilities": {}},
            }
        )
        self.assertEqual(response["result"]["protocolVersion"], "2026-07-28")

    def test_notification_gets_202_without_body(self) -> None:
        """JSON-RPC notifications (no ``id``) are acknowledged, not answered."""
        response = self.client.rest_post(
            "mcp",
            data=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode("utf-8"),
        )
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(response.content, b"")

    def test_non_object_body_is_rejected_by_rest_layer(self) -> None:
        """A JSON array body (JSON-RPC batch) dies at the REST layer as 400.

        Like malformed JSON, a non-object body never reaches the MCP
        handler: the REST processors contract requires JSON object bodies.
        The MCP-level dict guard stays as defense in depth.
        """
        response = self.client.rest_post("mcp", data=json.dumps([{"jsonrpc": "2.0", "id": 1}]).encode("utf-8"))
        self.assertEqual(response.status_code, 400, response.content)
        body = typing.cast(_JsonRpcObject, json.loads(response.content))
        # REST-level error, not JSON-RPC: no ``jsonrpc`` field.
        self.assertNotIn("jsonrpc", body)

    def test_ping_returns_empty_object(self) -> None:
        """``ping`` echoes the JSON-RPC envelope with an empty result."""
        response = self._post_jsonrpc({"jsonrpc": "2.0", "id": "abc", "method": "ping"})
        self.assertEqual(response, {"jsonrpc": "2.0", "id": "abc", "result": {}})

    def test_unknown_method_returns_method_not_found(self) -> None:
        """Unknown methods produce ``-32601`` and include the bad method."""
        response = self._post_jsonrpc({"jsonrpc": "2.0", "id": 7, "method": "foo/bar"})

        self.assertEqual(response["jsonrpc"], "2.0")
        self.assertEqual(response["id"], 7)
        self.assertEqual(response["error"]["code"], -32601)
        self.assertIn("foo/bar", response["error"]["message"])

    def test_resources_list_returns_curated_catalog(self) -> None:
        """``resources/list`` exposes only the curated read-only resources."""
        response = self._post_jsonrpc({"jsonrpc": "2.0", "id": 10, "method": "resources/list"})

        resources = response["result"]["resources"]
        self.assertEqual(
            [resource["uri"] for resource in resources],
            ["uds://system/overview", "uds://version"],
        )
        self.assertNotIn("token", json.dumps(response))

    def test_resources_read_uses_rest_proxy(self) -> None:
        """``resources/read`` returns data from an existing REST handler."""
        response = self._post_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "resources/read",
                "params": {"uri": "uds://version"},
            }
        )

        contents = response["result"]["contents"]
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0]["uri"], "uds://version")
        self.assertIn("version", contents[0]["text"].lower())

    def test_resources_read_system_overview_uses_rest_permissions(self) -> None:
        """The system overview resource is served by the existing admin REST handler."""
        response = self._post_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "resources/read",
                "params": {"uri": "uds://system/overview"},
            }
        )

        content = response["result"]["contents"][0]["text"]
        overview = json.loads(content)
        self.assertIn("users", overview)
        self.assertIn("services", overview)

    def test_invalid_json_returns_rest_400(self) -> None:
        """A non-JSON body never reaches the MCP handler.

        The REST dispatcher validates the ``application/json`` body
        before invoking the handler, so a malformed JSON returns a plain
        REST 400 rather than a JSON-RPC ``-32700``. This is intentional:
        the body has to be valid JSON to even hit the MCP surface.
        """
        response = self.client.rest_post("mcp", data=b"{not json")
        self.assertEqual(response.status_code, 400, response.content)
        body = typing.cast(_JsonRpcObject, json.loads(response.content))
        # REST-level error, not JSON-RPC: no ``jsonrpc`` field.
        self.assertEqual(body, {"error": "Invalid parameters"})

    def test_missing_method_returns_invalid_request(self) -> None:
        """A JSON-RPC envelope without ``method`` returns ``-32600``."""
        response = self._post_jsonrpc({"jsonrpc": "2.0", "id": 9})
        self.assertEqual(response["error"]["code"], -32600)
        self.assertEqual(response["id"], 9)

    def test_tools_list_exposes_master_and_detail_collections(self) -> None:
        """``tools/list`` includes master and parent-scoped detail tools."""
        response = self._post_jsonrpc({"jsonrpc": "2.0", "id": 20, "method": "tools/list"})
        tools: dict[str, dict[str, typing.Any]] = {
            tool["name"]: tool for tool in typing.cast("list[dict[str, typing.Any]]", response["result"]["tools"])
        }
        self.assertIn("list_authenticators", tools)
        detail = tools.get("list_authenticators_users")
        self.assertIsNotNone(detail, "detail collection tool missing")
        if detail is not None:
            self.assertIn("parent_uuid", detail["inputSchema"]["properties"])

    def test_tools_list_only_publishes_model_collections(self) -> None:
        """Plain ``Handler`` collections (e.g. reports) are not published."""
        response = self._post_jsonrpc({"jsonrpc": "2.0", "id": 23, "method": "tools/list"})
        tools = {tool["name"] for tool in typing.cast("list[dict[str, typing.Any]]", response["result"]["tools"])}
        self.assertNotIn("list_reports", tools)

    def test_tools_call_detail_collection_lists_parent_users(self) -> None:
        """``tools/call`` on a detail tool lists the parent's items."""
        response = self._post_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 21,
                "method": "tools/call",
                "params": {
                    "name": "list_authenticators_users",
                    "arguments": {"parent_uuid": self.auth.uuid},
                },
            }
        )
        self.assertNotIn("error", response)
        result = response["result"]
        # The detail collection returns the authenticator's users.
        content = json.loads(result["content"][0]["text"])
        self.assertIsInstance(content, list)
        self.assertTrue(content, "expected at least one user in the authenticator")

    def test_tools_call_detail_unknown_parent_errors(self) -> None:
        """A bogus parent uuid surfaces as a JSON-RPC error, not a crash."""
        response = self._post_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 22,
                "method": "tools/call",
                "params": {
                    "name": "list_authenticators_users",
                    "arguments": {"parent_uuid": "00000000-0000-0000-0000-000000000000"},
                },
            }
        )
        self.assertIn("error", response)
        self.assertEqual(response["id"], 22, "error responses must echo the request id")
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("Parent item not found", response["error"]["message"])

    def test_tools_call_unknown_tool_is_invalid_params(self) -> None:
        """An unknown tool name is an ``invalid params`` error echoing the id."""
        response = self._post_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 24,
                "method": "tools/call",
                "params": {"name": "list_nonexistent", "arguments": {}},
            }
        )
        self.assertIn("error", response)
        self.assertEqual(response["id"], 24)
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("list_nonexistent", response["error"]["message"])

    def test_tools_call_access_denied_is_server_error(self) -> None:
        """A user without the handler role gets ``-32000``, not invalid params."""
        self.login_with_api_token(user=self.plain_users[0])
        response = self._post_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 25,
                "method": "tools/call",
                "params": {"name": "list_authenticators", "arguments": {}},
            }
        )
        self.assertIn("error", response)
        self.assertEqual(response["id"], 25)
        self.assertEqual(response["error"]["code"], -32000)
        self.assertEqual(response["error"]["message"], "Access denied")

    def test_tools_list_invalid_cursor_is_invalid_params(self) -> None:
        """A malformed pagination cursor surfaces as ``-32602``."""
        response = self._post_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 28,
                "method": "tools/list",
                "params": {"cursor": "%%% not base64 %%%"},
            }
        )
        self.assertIn("error", response)
        self.assertEqual(response["id"], 28)
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("cursor", response["error"]["message"].lower())

    def test_tools_call_rejects_argument_of_wrong_type(self) -> None:
        """Arguments are validated against the published input schema."""
        response = self._post_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 29,
                "method": "tools/call",
                "params": {"name": "list_authenticators", "arguments": {"top": "abc"}},
            }
        )
        self.assertIn("error", response)
        self.assertEqual(response["id"], 29)
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("top", response["error"]["message"])

    def test_tools_call_rejects_unknown_argument(self) -> None:
        """Arguments not present in the schema are rejected up front."""
        response = self._post_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 30,
                "method": "tools/call",
                "params": {"name": "list_authenticators", "arguments": {"bogus": 1}},
            }
        )
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("bogus", response["error"]["message"])

    def test_resources_read_unknown_uri_is_resource_not_found(self) -> None:
        """An unknown resource URI returns ``-32002`` inside a JSON-RPC envelope."""
        response = self._post_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 26,
                "method": "resources/read",
                "params": {"uri": "uds://nonexistent"},
            }
        )
        self.assertIn("error", response)
        self.assertEqual(response["id"], 26)
        self.assertEqual(response["error"]["code"], -32002)
        self.assertIn("uds://nonexistent", response["error"]["message"])


class MCPRestEquivalenceTest(rest.test.RESTTestCase):
    """The result of a read tool must match its REST endpoint twin.

    Equivalence is asserted modulo MCP redaction: the REST payload is
    passed through the same ``redact()`` the MCP core applies, so both
    sides are compared on equal terms.
    """

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        GlobalConfig.MCP_ENABLED.set(True)
        self.login_with_api_token()

    def _post_jsonrpc(self, body: dict[str, object]) -> _JsonRpcObject:
        response = self.client.rest_post("mcp", data=json.dumps(body).encode("utf-8"))
        self.assertEqual(response.status_code, 200, response.content)
        return typing.cast(_JsonRpcObject, json.loads(response.content))

    def _rest_list(self, path: str, query: dict[str, str] | None = None) -> list[typing.Any]:
        response = self.client.rest_get(path, data=query or {})
        self.assertEqual(response.status_code, 200, response.content)
        return typing.cast("list[typing.Any]", response.json())

    def _mcp_list(self, tool: str, arguments: dict[str, typing.Any]) -> list[typing.Any]:
        response = self._post_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
        )
        self.assertNotIn("error", response)
        return typing.cast("list[typing.Any]", json.loads(response["result"]["content"][0]["text"]))

    def test_master_collections_match_rest(self) -> None:
        for tool, path in (("list_authenticators", "authenticators"), ("list_providers", "providers")):
            with self.subTest(tool=tool):
                self.assertEqual(
                    self._mcp_list(tool, {}),
                    redact(self._rest_list(path)),
                )

    def test_detail_collection_matches_rest(self) -> None:
        rest_items = redact(self._rest_list(f"authenticators/{self.auth.uuid}/users"))
        mcp_items = self._mcp_list("list_authenticators_users", {"parent_uuid": self.auth.uuid})
        self.assertEqual(mcp_items, rest_items)

    def test_odata_arguments_match_rest_query(self) -> None:
        query = {"$filter": "contains(name, 'user')", "$top": "2", "$orderby": "name"}
        arguments = {"filter": "contains(name, 'user')", "top": 2, "orderby": "name"}
        rest_items = redact(self._rest_list(f"authenticators/{self.auth.uuid}/users", query))
        mcp_items = self._mcp_list("list_authenticators_users", {"parent_uuid": self.auth.uuid, **arguments})
        self.assertEqual(mcp_items, rest_items)
        self.assertLessEqual(len(mcp_items), 2)

    def test_resources_read_access_denied_is_jsonrpc_error(self) -> None:
        """A permission error stays a JSON-RPC envelope instead of a REST 403."""
        self.login_with_api_token(user=self.plain_users[0])
        response = self._post_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 27,
                "method": "resources/read",
                "params": {"uri": "uds://system/overview"},
            }
        )
        self.assertIn("error", response)
        self.assertEqual(response["id"], 27)
        self.assertEqual(response["error"]["code"], -32000)
        self.assertEqual(response["error"]["message"], "Access denied")
