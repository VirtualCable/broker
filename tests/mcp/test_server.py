"""Tests for the low-level MCP server adapter."""

import unittest
import typing

import mcp.types

from uds.mcp import Catalog, MCPServerCore, ResourceDefinition, ToolDefinition


class MCPServerCoreTest(unittest.IsolatedAsyncioTestCase):
    """Verify catalog entries are exposed through MCP protocol results."""

    async def test_lists_catalog_tools_and_resources(self) -> None:
        """Tool and resource metadata is converted to MCP types."""
        catalog = Catalog()

        async def read_status(_arguments: dict[str, object]) -> dict[str, object]:
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

        async def read_credentials(_arguments: dict[str, object]) -> dict[str, object]:
            return {"name": "service", "token": "secret"}

        catalog.add_tool(
            ToolDefinition("credentials", "Credentials", "Read data", {}, "platform", "data", executor=read_credentials)
        )
        core = MCPServerCore(catalog)

        result = await core.call_tool(None, mcp.types.CallToolRequestParams(name="credentials"))

        self.assertEqual(result.structured_content, {"name": "service", "token": "REDACTED"})
        self.assertNotIn("secret", typing.cast(mcp.types.TextContent, result.content[0]).text)
