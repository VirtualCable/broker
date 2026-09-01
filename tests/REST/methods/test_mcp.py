"""Functional tests for the JSON-RPC endpoint exposed at ``/uds/rest/mcp``.

The handler currently only implements the ``initialize`` and ``ping`` MCP
methods. These tests pin the JSON-RPC contract (envelope, error codes,
version) so the follow-up wiring of the catalogue does not regress them.
"""

import json
import typing

from tests.utils import rest


_JsonRpcObject = dict[str, typing.Any]


class MCPRPCTest(rest.test.RESTTestCase):
    """Pin the JSON-RPC envelope of the MCP endpoint."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login_with_api_token()

    def _post_jsonrpc(self, body: dict[str, object]) -> _JsonRpcObject:
        response = self.client.rest_post("mcp", data=json.dumps(body).encode("utf-8"))
        self.assertEqual(response.status_code, 200, response.content)
        return typing.cast(_JsonRpcObject, json.loads(response.content))

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
