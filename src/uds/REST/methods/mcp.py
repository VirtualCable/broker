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
* dispatches ``tools/list``, ``tools/call``, ``resources/list`` and
  ``resources/read`` to :class:`uds.mcp.MCPServerCore`, backed by the
  process-wide catalog built from the REST handler inventory;
* negotiates the MCP protocol version in ``initialize`` and acknowledges
  JSON-RPC notifications (messages without ``id``) with an empty
  ``202 Accepted``, as the Streamable HTTP transport prescribes;
* translates domain errors into JSON-RPC error envelopes that echo the
  request ``id``, and returns the standard ``-32601 Method not found``
  for unknown methods, instead of a generic HTTP 400.
"""

import logging
import typing

import mcp.types
from asgiref.sync import async_to_sync
from django import http

from uds.core import consts
from uds.core.exceptions import rest as rest_exceptions
from uds.REST import Handler
from uds.mcp import MCPServerCore, get_catalog


logger = logging.getLogger(__name__)

# Latest protocol version we speak, taken from the SDK so the two never
# drift apart.
_MCP_PROTOCOL_VERSION: typing.Final[str] = mcp.types.LATEST_PROTOCOL_VERSION

# Protocol revisions the server can speak. The surface we implement
# (initialize/ping/tools/resources over JSON-RPC) is identical in both;
# clients asking for anything else get the latest and decide whether to
# keep going, as the MCP specification prescribes.
_SUPPORTED_PROTOCOL_VERSIONS: typing.Final[frozenset[str]] = frozenset(
    {
        _MCP_PROTOCOL_VERSION,
        "2025-06-18",
    }
)

# Server identity reported during the ``initialize`` handshake.
_MCP_SERVER_NAME: typing.Final[str] = "UDS"
_MCP_SERVER_VERSION: typing.Final[str] = "0.1.0"

# JSON-RPC 2.0 error codes used by this endpoint.
_JSONRPC_INVALID_REQUEST: typing.Final[int] = -32600
_JSONRPC_METHOD_NOT_FOUND: typing.Final[int] = -32601
_JSONRPC_INVALID_PARAMS: typing.Final[int] = -32602

# Server-defined error codes reserved by the MCP specification.
# ``-32000`` is the generic server error slot; ``-32002`` means
# "Resource not found" for ``resources/read``.
_MCP_SERVER_ERROR: typing.Final[int] = -32000
_MCP_RESOURCE_NOT_FOUND: typing.Final[int] = -32002

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

    def post(self) -> _JsonObject | http.HttpResponse:
        """Process a single JSON-RPC message and return its JSON-RPC response.

        The REST dispatcher has already decoded the JSON body into
        ``self._params`` by the time we get here, so we never have to
        read or parse ``self._request.body`` again. If the body could
        not be parsed, the dispatcher returns 400 before invoking us,
        which is the right behaviour.

        JSON-RPC notifications (a message without ``id``, such as
        ``notifications/initialized``) get no response body: per the
        MCP Streamable HTTP transport they are acknowledged with an
        empty ``202 Accepted``. Domain errors raised while serving a
        JSON-RPC request are always translated into a JSON-RPC error
        envelope echoing the request ``id``; they never leak as plain
        REST error responses.
        """
        # The ``_params`` annotation says dict, but the dispatcher stores
        # whatever the JSON body decoded to, so the runtime check matters.
        if not isinstance(self._params, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            # JSON-RPC batches and other non-object bodies are not
            # supported; answer at the protocol level instead of crashing.
            return self._jsonrpc_error(None, _JSONRPC_INVALID_REQUEST, "JSON-RPC body must be a single object")

        params: _JsonObject = self._params

        if "id" not in params:
            # A message without ``id`` is a notification: no response is
            # allowed, acknowledge with 202 and an empty body.
            return http.HttpResponse(status=202)

        method_obj: typing.Any = params.get("method")
        request_id: typing.Any = params.get("id")

        if not isinstance(method_obj, str):
            return self._jsonrpc_error(request_id, _JSONRPC_INVALID_REQUEST, "Missing JSON-RPC method")
        method = method_obj

        if method == "initialize":
            return self._initialize(params, request_id)

        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}

        if method == "resources/list":
            try:
                list_params = mcp.types.PaginatedRequestParams.model_validate(params.get("params") or {})
                result = async_to_sync(self._mcp_server().list_resources)(None, list_params)
            except ValueError as exc:
                return self._jsonrpc_error(request_id, _JSONRPC_INVALID_PARAMS, str(exc))
            return self._model_response(request_id, result)

        if method == "resources/read":
            try:
                read_params = mcp.types.ReadResourceRequestParams.model_validate(params.get("params") or {})
            except ValueError as exc:
                return self._jsonrpc_error(request_id, _JSONRPC_INVALID_PARAMS, str(exc))
            try:
                result = async_to_sync(self._mcp_server().read_resource)(None, read_params)
            except rest_exceptions.HandlerError as exc:
                code, message = self._map_handler_error(exc, not_found_code=_MCP_RESOURCE_NOT_FOUND)
                return self._jsonrpc_error(request_id, code, message)
            except ValueError as exc:
                return self._jsonrpc_error(request_id, _MCP_RESOURCE_NOT_FOUND, str(exc))
            return self._model_response(request_id, result)

        if method == "tools/list":
            try:
                list_params = mcp.types.PaginatedRequestParams.model_validate(params.get("params") or {})
                result = async_to_sync(self._mcp_server().list_tools)(None, list_params)
            except ValueError as exc:
                return self._jsonrpc_error(request_id, _JSONRPC_INVALID_PARAMS, str(exc))
            return self._model_response(request_id, result)

        if method == "tools/call":
            call_params = mcp.types.CallToolRequestParams.model_validate(params.get("params") or {})
            try:
                result = async_to_sync(self._mcp_server().call_tool)(None, call_params)
            except rest_exceptions.HandlerError as exc:
                code, message = self._map_handler_error(exc, not_found_code=_JSONRPC_INVALID_PARAMS)
                return self._jsonrpc_error(request_id, code, message)
            except ValueError as exc:
                # Unknown tool names are an ``invalid params`` error per
                # the MCP ``tools/call`` contract.
                return self._jsonrpc_error(request_id, _JSONRPC_INVALID_PARAMS, str(exc))
            return self._model_response(request_id, result)

        return self._jsonrpc_error(
            request_id,
            _JSONRPC_METHOD_NOT_FOUND,
            f"Method not implemented: {method}",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _mcp_server(self) -> MCPServerCore:
        """Build the catalog-backed MCP core for the current request.

        The catalog itself is process-wide (the REST tree does not
        change at runtime); only the live HTTP request is per-call.
        """
        return MCPServerCore(get_catalog(), request=self._request)

    @staticmethod
    def _map_handler_error(
        exc: rest_exceptions.HandlerError,
        *,
        not_found_code: int,
    ) -> tuple[int, str]:
        """Return the JSON-RPC error code and message for a REST error.

        Permission problems are server errors, not bad arguments, so
        ``AccessDenied`` maps to the MCP server error slot. Argument
        validation failures (``RequestError``) and unknown items
        (``NotFound``) map to ``invalid params`` for tools; callers pass
        the code a ``NotFound`` should produce in their method context
        (``-32002`` "Resource not found" for ``resources/read``).
        """
        if isinstance(exc, rest_exceptions.AccessDenied):
            return _MCP_SERVER_ERROR, "Access denied"
        if isinstance(exc, rest_exceptions.NotFound):
            return not_found_code, str(exc) or "Not found"
        if isinstance(exc, rest_exceptions.RequestError):
            return _JSONRPC_INVALID_PARAMS, str(exc) or "Invalid arguments"
        return _MCP_SERVER_ERROR, str(exc) or exc.__class__.__name__

    @staticmethod
    def _model_response(request_id: typing.Any, result: typing.Any) -> _JsonObject:
        """Convert an MCP SDK result model into a JSON-RPC response."""
        return {"jsonrpc": "2.0", "id": request_id, "result": result.model_dump(by_alias=True, exclude_none=True)}

    @staticmethod
    def _initialize(
        message: _JsonObject,
        request_id: typing.Any,
    ) -> _JsonObject:
        """Respond to the JSON-RPC ``initialize`` handshake.

        Negotiates the protocol version per the MCP specification: if the
        client requests a revision we support, we answer with that exact
        revision; otherwise we answer with our latest and let the client
        decide whether to continue.
        """
        raw_params: typing.Any = message.get("params") or {}
        requested_protocol: str | None = None
        if isinstance(raw_params, dict):
            params_dict: dict[str, typing.Any] = typing.cast(dict[str, typing.Any], raw_params)
            raw_protocol: typing.Any = params_dict.get("protocolVersion")
            if isinstance(raw_protocol, str):
                requested_protocol = raw_protocol

        negotiated = (
            requested_protocol
            if requested_protocol is not None and requested_protocol in _SUPPORTED_PROTOCOL_VERSIONS
            else _MCP_PROTOCOL_VERSION
        )

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": negotiated,
                "serverInfo": {"name": _MCP_SERVER_NAME, "version": _MCP_SERVER_VERSION},
                "capabilities": {
                    "tools": {},
                    "resources": {},
                },
                "instructions": f"UDS MCP server ready (protocol {negotiated})",
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
