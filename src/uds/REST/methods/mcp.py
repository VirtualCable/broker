"""
MCP handler.

The Model Context Protocol (MCP) speaks JSON-RPC 2.0 over HTTP. The transport
(Streamable HTTP, plain JSON, SSE, ...) is configured at the network edge;
what arrives at this handler is already a JSON-RPC message.

This module provides a thin handler that:

* reuses the REST dispatcher's already-parsed ``self._params`` (the JSON
  body has been decoded by ``processors.JsonProcessor`` before our
  handler runs, so we never have to read or parse the body again);
* recognises the JSON-RPC envelope (``jsonrpc``, ``id``, ``method``,
  ``params``);
* responds to ``initialize`` with the server's identity and capabilities;
* returns the standard JSON-RPC ``-32601 Method not found`` for unknown
  methods, instead of a generic HTTP 400.

The real MCP method handlers (``tools/list``, ``tools/call``,
``resources/list``, ``resources/read``) are wired in a follow-up once the
catalog lives in ``uds.mcp``. This file keeps the HTTP contract stable so
that future iterations can plug the SDK without breaking clients.
"""

import logging
import typing

import mcp.types
from asgiref.sync import async_to_sync

from uds.core import consts
from uds.core.exceptions import rest as rest_exceptions
from uds.REST import Handler
from uds.mcp import MCPServerCore, default_catalog_for_request


logger = logging.getLogger(__name__)

# Protocol version reported to clients during the ``initialize`` handshake.
# Matches the MCP 2026-07-28 revision we target.
_MCP_PROTOCOL_VERSION: typing.Final[str] = "2026-07-28"

# Server identity reported during the ``initialize`` handshake.
_MCP_SERVER_NAME: typing.Final[str] = "UDS"
_MCP_SERVER_VERSION: typing.Final[str] = "0.1.0"

# After ``self._params`` the structure is opaque to pyright; cast helpers
# keep the rest of the module clean of repeated ``cast()`` calls.
_JsonObject = dict[str, typing.Any]


class MCP(Handler):
    """Expose the UDS Model Context Protocol surface over REST.

    The handler lives under ``/uds/rest/mcp`` and is registered automatically
    by the REST dispatcher because it inherits from :class:`Handler` and is
    placed under ``uds.REST.methods``.

    The MCP transport is intentionally out of scope here: the HTTP layer
    delivers a fully-formed JSON-RPC request and we answer with a fully-
    formed JSON-RPC response. SSE streaming, session negotiation, and the
    Streamable HTTP transport will be added in a follow-up once the
    catalogue is wired in.
    """

    # Mount under ``/uds/rest/mcp`` using the default NAME (class name in
    # lower case). The default behaviour already gives a single-segment
    # path at the REST root, which is what we want for a global MCP
    # endpoint that does not belong to any specific collection.

    ROLE: typing.ClassVar[consts.Role] = consts.Role.USER

    def post(self) -> _JsonObject:
        """Process a single JSON-RPC request and return the JSON-RPC response.

        The REST dispatcher has already decoded the JSON body into
        ``self._params`` by the time we get here, so we never have to
        read or parse ``self._request.body`` again. If the body could
        not be parsed, the dispatcher returns 400 before invoking us,
        which is the right behaviour.
        """
        params: _JsonObject = self._params

        method_obj: typing.Any = params.get("method")
        request_id: typing.Any = params.get("id")

        if not isinstance(method_obj, str):
            return self._jsonrpc_error(request_id, -32600, "Missing JSON-RPC method")
        method = method_obj

        if method == "initialize":
            return self._initialize(params, request_id)

        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}

        if method == "resources/list":
            result = async_to_sync(self._mcp_server().list_resources)(None, None)
            return self._model_response(request_id, result)

        if method == "resources/read":
            read_params = mcp.types.ReadResourceRequestParams.model_validate(params.get("params") or {})
            result = async_to_sync(self._mcp_server().read_resource)(None, read_params)
            return self._model_response(request_id, result)

        if method == "tools/list":
            result = async_to_sync(self._mcp_server().list_tools)(None, None)
            return self._model_response(request_id, result)

        if method == "tools/call":
            call_params = mcp.types.CallToolRequestParams.model_validate(params.get("params") or {})
            try:
                result = async_to_sync(self._mcp_server().call_tool)(None, call_params)
            except rest_exceptions.HandlerError as exc:
                # REST-domain errors (item not found, access denied, invalid
                # request) surface as JSON-RPC errors with a readable message
                # instead of leaking as a transport-level failure.
                return self._jsonrpc_error(call_params.name, -32602, str(exc) or exc.__class__.__name__)
            except ValueError as exc:
                return self._jsonrpc_error(call_params.name, -32602, str(exc))
            return self._model_response(request_id, result)

        return self._jsonrpc_error(
            request_id,
            -32601,
            f"Method not implemented: {method}",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _mcp_server(self) -> MCPServerCore:
        """Build the catalog-backed MCP core for the current request."""
        return MCPServerCore(default_catalog_for_request(self._request), request=self._request)

    @staticmethod
    def _model_response(request_id: typing.Any, result: typing.Any) -> _JsonObject:
        """Convert an MCP SDK result model into a JSON-RPC response."""
        return {"jsonrpc": "2.0", "id": request_id, "result": result.model_dump(by_alias=True, exclude_none=True)}

    @staticmethod
    def _initialize(
        message: _JsonObject,
        request_id: typing.Any,
    ) -> _JsonObject:
        """Respond to the JSON-RPC ``initialize`` handshake."""
        raw_params: typing.Any = message.get("params") or {}
        client_protocol: str | None = None
        if isinstance(raw_params, dict):
            params_dict: dict[str, typing.Any] = typing.cast(dict[str, typing.Any], raw_params)
            raw_protocol: typing.Any = params_dict.get("protocolVersion")
            if isinstance(raw_protocol, str):
                client_protocol = raw_protocol

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "serverInfo": {"name": _MCP_SERVER_NAME, "version": _MCP_SERVER_VERSION},
                "capabilities": {
                    # Tools and resources become available once the
                    # MCP catalog is wired in a follow-up commit.
                    "tools": {},
                    "resources": {},
                },
                # Echo the negotiated protocol when the client asks for a
                # known one; otherwise stick to the protocol version we
                # speak. The MCP spec expects clients to handle either.
                "instructions": (
                    f"Negotiated MCP protocol {client_protocol}" if client_protocol else "UDS MCP server ready"
                ),
            },
        }

    @staticmethod
    def _jsonrpc_error(
        request_id: typing.Any,
        code: int,
        message: str,
    ) -> _JsonObject:
        """Build a JSON-RPC error envelope."""
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
