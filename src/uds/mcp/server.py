"""MCP protocol server backed by the curated UDS catalog."""

import json
import typing

import mcp.types
from mcp.server.lowlevel import Server

from .catalog import Catalog
from .redaction import redact


class MCPServerCore:
    """Expose catalog entries through the low-level MCP server API."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self.server = Server(
            "UDS",
            on_list_tools=self.list_tools,
            on_call_tool=self.call_tool,
            on_list_resources=self.list_resources,
            on_read_resource=self.read_resource,
        )

    async def list_tools(
        self,
        _context: typing.Any,
        _params: typing.Any,
    ) -> mcp.types.ListToolsResult:
        """Return the stable MCP tool list generated from the catalog."""
        return mcp.types.ListToolsResult(
            tools=[
                mcp.types.Tool(
                    name=tool.name,
                    title=tool.title,
                    description=tool.description,
                    input_schema=tool.input_schema,
                    _meta={
                        "uds": {
                            "access": tool.access,
                            "returns": tool.returns,
                            "required_permission": tool.required_permission,
                            "read_only": tool.read_only,
                        }
                    },
                )
                for tool in self.catalog.tools()
            ]
        )

    async def call_tool(
        self,
        _context: typing.Any,
        params: mcp.types.CallToolRequestParams,
    ) -> mcp.types.CallToolResult:
        """Execute a catalog tool and redact its result."""
        tool = self.catalog.get_tool(params.name)
        if tool is None or tool.executor is None:
            raise ValueError(f"Unknown MCP tool: {params.name}")

        result = await tool.executor(params.arguments or {})
        safe_result = redact(result)
        return mcp.types.CallToolResult(
            content=[mcp.types.TextContent(text=json.dumps(safe_result, default=str))],
            structured_content=safe_result,
        )

    async def list_resources(
        self,
        _context: typing.Any,
        _params: typing.Any,
    ) -> mcp.types.ListResourcesResult:
        """Return the stable MCP resource list generated from the catalog."""
        return mcp.types.ListResourcesResult(
            resources=[
                mcp.types.Resource(
                    uri=resource.uri,
                    name=resource.name,
                    title=resource.title,
                    description=resource.description,
                )
                for resource in self.catalog.resources()
            ]
        )

    async def read_resource(
        self,
        _context: typing.Any,
        params: mcp.types.ReadResourceRequestParams,
    ) -> mcp.types.ReadResourceResult:
        """Read a catalog resource and redact its contents."""
        resource = self.catalog.get_resource(params.uri)
        if resource is None or resource.reader is None:
            raise ValueError(f"Unknown MCP resource: {params.uri}")

        content = await resource.reader(params.uri)
        return mcp.types.ReadResourceResult(
            contents=typing.cast(
                list[mcp.types.TextResourceContents | mcp.types.BlobResourceContents],
                [
                    mcp.types.TextResourceContents(
                        uri=params.uri,
                        mime_type="text/plain",
                        text=str(redact(content)),
                    )
                ],
            )
        )
