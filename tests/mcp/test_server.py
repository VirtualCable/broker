"""Tests for the low-level MCP server adapter."""

import unittest
import typing
from unittest import mock

import mcp.types

from uds.mcp import Catalog, MCPServerCore, ResourceDefinition, ToolDefinition


class MCPServerCoreTest(unittest.IsolatedAsyncioTestCase):
    """Verify catalog entries are exposed through MCP protocol results."""

    async def test_lists_catalog_tools_and_resources(self) -> None:
        """Tool and resource metadata is converted to MCP types."""
        catalog = Catalog()

        async def read_status(_arguments: dict[str, object], _request: typing.Any = None) -> dict[str, object]:
            return {"status": "ok"}

        async def read_resource(_uri: str) -> str:
            return "status: ok"

        catalog.add_tool(
            ToolDefinition("status", "Status", "Read status", {}, "platform", "status", executor=read_status)
        )
        catalog.add_resource(
            ResourceDefinition(
                "uds://status", "status", "Status", "Platform status", "platform", "status", reader=read_resource
            )
        )
        core = MCPServerCore(catalog)

        tools = await core.list_tools(None, None)
        resources = await core.list_resources(None, None)

        self.assertEqual([tool.name for tool in tools.tools], ["status"])
        self.assertEqual([resource.uri for resource in resources.resources], ["uds://status"])

    async def test_call_tool_redacts_result(self) -> None:
        """Tool results are redacted before being returned as MCP content."""
        catalog = Catalog()

        async def read_credentials(_arguments: dict[str, object], _request: typing.Any = None) -> dict[str, object]:
            return {"name": "service", "token": "secret"}

        catalog.add_tool(
            ToolDefinition("credentials", "Credentials", "Read data", {}, "platform", "data", executor=read_credentials)
        )
        core = MCPServerCore(catalog)

        result = await core.call_tool(None, mcp.types.CallToolRequestParams(name="credentials"))

        self.assertEqual(result.structured_content, {"name": "service", "token": "REDACTED"})
        self.assertNotIn("secret", typing.cast(mcp.types.TextContent, result.content[0]).text)

    async def test_call_tool_forwards_request_to_executor(self) -> None:
        """The live HTTP request bound to the core reaches the executor."""
        catalog = Catalog()
        seen: list[typing.Any] = []

        async def echo_request(_arguments: dict[str, object], request: typing.Any = None) -> dict[str, object]:
            seen.append(request)
            return {"ok": True}

        catalog.add_tool(ToolDefinition("echo", "Echo", "Echo request", {}, "platform", "ok", executor=echo_request))
        sentinel = object()
        core = MCPServerCore(catalog, request=sentinel)

        await core.call_tool(None, mcp.types.CallToolRequestParams(name="echo"))

        self.assertIs(seen[0], sentinel)

    async def test_call_tool_validates_arguments_against_schema(self) -> None:
        """Arguments that do not match the published schema fail fast."""
        catalog = Catalog()

        async def never_called(_arguments: dict[str, object], _request: typing.Any = None) -> dict[str, object]:
            raise AssertionError("executor must not run for invalid arguments")

        schema = {"type": "object", "properties": {"top": {"type": "integer"}}, "additionalProperties": False}
        catalog.add_tool(
            ToolDefinition("listed", "Listed", "List items", schema, "users", "items", executor=never_called)
        )
        core = MCPServerCore(catalog)

        with self.assertRaises(ValueError) as ctx:
            await core.call_tool(None, mcp.types.CallToolRequestParams(name="listed", arguments={"top": "abc"}))
        self.assertIn("top", str(ctx.exception))

    async def test_list_tools_is_paginated(self) -> None:
        """``tools/list`` pages through the catalog with an opaque cursor."""
        catalog = Catalog()
        for name in ("a", "b", "c", "d"):
            catalog.add_tool(ToolDefinition(name, name.upper(), f"Tool {name}", {}, "users", "items"))
        core = MCPServerCore(catalog)

        with mock.patch("uds.mcp.server._PAGE_SIZE", 2):
            first = await core.list_tools(None, mcp.types.PaginatedRequestParams())
            self.assertEqual([t.name for t in first.tools], ["a", "b"])
            self.assertIsNotNone(first.next_cursor)

            second = await core.list_tools(None, mcp.types.PaginatedRequestParams(cursor=first.next_cursor or ""))
            self.assertEqual([t.name for t in second.tools], ["c", "d"])
            self.assertIsNone(second.next_cursor)

    async def test_list_tools_rejects_garbage_cursor(self) -> None:
        """A malformed cursor surfaces as ``invalid params`` (ValueError)."""
        core = MCPServerCore(Catalog())
        with self.assertRaises(ValueError):
            await core.list_tools(None, mcp.types.PaginatedRequestParams(cursor="%%% not base64 %%%"))
