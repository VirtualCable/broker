"""Live-protocol tests for the MCP endpoint served over real HTTP.

Two kinds of coverage live here:

* **Verb and header contract**: a GET on the MCP endpoint (the Streamable
  HTTP server-to-client stream, which UDS does not offer) and a DELETE
  (session termination, not supported by our stateless server) must answer
  ``405 Method Not Allowed`` with an ``Allow`` header. Today that behaviour
  comes from the REST dispatcher reacting to the handler not defining those
  methods; these tests turn it into a contract so a future dispatcher-wide
  GET-to-POST compatibility bridge cannot silently change it. The ``Accept``
  negotiation (``406`` when JSON is ruled out) is pinned here as well.
* **Real MCP client**: the official ``mcp`` SDK client (the same package the
  server imports its types from) performs a full session against a live
  server: ``initialize`` handshake, ``tools/list`` and ``tools/call``. This
  proves any standards-compliant client — opencode included, whose MCP stack
  speaks the same protocol — can drive the endpoint, without needing a real
  LLM.

The broker runs in-process through Django's ``LiveServerTestCase`` with the
standard REST fixtures, so the data is controlled and the assertions are
deterministic.
"""

import asyncio
import typing

import httpx
import httpx2
from django.test import LiveServerTestCase
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from uds.core.util.config import GlobalConfig

from tests.utils import rest


class McpLiveProtocolTest(rest.test.RESTTestCase, LiveServerTestCase):  # pyright: ignore[reportIncompatibleVariableOverride]  # client is UDSClient
    """Drive the MCP endpoint over real HTTP with real clients."""

    mcp_url: str
    token: str

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        GlobalConfig.MCP_ENABLED.set(True)
        self.token = self.login_with_api_token()
        self.mcp_url = f"{self.live_server_url}/uds/rest/mcp"

    def _headers(self, **extra: str) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}"}
        headers.update(extra)
        return headers

    # ------------------------------------------------------------------
    # Verb contract (pinned, see module docstring)
    # ------------------------------------------------------------------
    def test_get_returns_method_not_allowed(self) -> None:
        """GET is the SSE stream we do not offer: 405 + Allow with POST."""
        response = httpx.get(self.mcp_url, headers=self._headers())
        self.assertEqual(response.status_code, 405, response.text)
        self.assertIn("POST", response.headers.get("Allow", ""))

    def test_delete_returns_method_not_allowed(self) -> None:
        """DELETE would terminate a session; we are stateless: 405."""
        response = httpx.delete(self.mcp_url, headers=self._headers())
        self.assertEqual(response.status_code, 405, response.text)

    # ------------------------------------------------------------------
    # Content negotiation
    # ------------------------------------------------------------------
    def test_accept_ruling_out_json_is_not_acceptable(self) -> None:
        """A client that cannot accept JSON gets 406, per the transport."""
        response = httpx.post(
            self.mcp_url,
            headers=self._headers(Accept="text/plain"),
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        self.assertEqual(response.status_code, 406, response.text)

    def test_standard_mcp_accept_gets_json(self) -> None:
        """The Accept value every MCP client sends works and gets JSON."""
        response = httpx.post(
            self.mcp_url,
            headers=self._headers(Accept="application/json, text/event-stream"),
            json={"jsonrpc": "2.0", "id": 2, "method": "ping"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        self.assertEqual(response.json()["id"], 2)

    # ------------------------------------------------------------------
    # Official SDK client end-to-end
    # ------------------------------------------------------------------
    def test_sdk_client_handshake_lists_and_calls_tools(self) -> None:
        """A real MCP client completes a full session against the broker."""
        asyncio.run(self._sdk_session())

    async def _sdk_session(self) -> None:
        """Run one SDK client session: initialize, list tools, call a tool."""
        # The SDK v2 HTTP stack is httpx2; build its client directly so the
        # Authorization header travels on every request (including the
        # server-to-client GET stream probe).
        async with (
            httpx2.AsyncClient(headers={"Authorization": f"Bearer {self.token}"}) as http_client,
            streamable_http_client(self.mcp_url, http_client=http_client) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            initialized = await session.initialize()
            self.assertEqual(initialized.server_info.name, "UDS")
            self.assertIsNotNone(initialized.capabilities.tools)
            self.assertIsNotNone(initialized.capabilities.resources)

            listed = await session.list_tools()
            tool_names = {tool.name for tool in listed.tools}
            self.assertIn("list_authenticators", tool_names)

            called = await session.call_tool("list_authenticators", {})
            self.assertFalse(called.is_error, called.content)
            text = "".join(getattr(content, "text", "") for content in called.content)
            self.assertIn(self.auth.name, text)
