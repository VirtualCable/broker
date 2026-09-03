"""MCP protocol core backed by the curated UDS catalog."""

import base64
import json
import typing
import collections.abc

import mcp.types

from uds.REST.processors import ContentProcessor

from .catalog import Catalog
from .redaction import redact
from .rest_proxy import RestProxy
from .validation import validate_arguments

# Maximum number of entries returned by ``tools/list`` and
# ``resources/list`` in a single page. Clients follow the opaque
# ``nextCursor`` to fetch the remaining pages.
_PAGE_SIZE: typing.Final[int] = 50


def _json_safe(value: typing.Any) -> typing.Any:
    """Normalize a handler result the same way REST renders it.

    REST handlers return ``BaseRestItem`` dataclasses, lazy translations,
    bytes and similar types; the REST processors know how to render them
    all. Routing MCP results through the very same normalisation keeps
    tool output and REST output equivalent.
    """
    return ContentProcessor.process_for_render(value, lambda d: d)


def _encode_cursor(item_name: str) -> str:
    """Return the opaque cursor pointing right after ``item_name``."""
    return base64.urlsafe_b64encode(item_name.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> str:
    """Return the item name encoded in an opaque page cursor."""
    try:
        return base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid list cursor") from exc


def _paginate[T](
    items: collections.abc.Sequence[T],
    cursor: str | None,
    name_of: collections.abc.Callable[[T], str],
) -> tuple[list[T], str | None]:
    """Return one page of ``items`` (sorted by ``name_of``) and the next cursor.

    ``cursor`` is the ``nextCursor`` value of a previous page; ``None``
    starts from the beginning. The next cursor is only produced when
    more entries remain after this page.
    """
    after = _decode_cursor(cursor) if cursor else None
    remaining = [item for item in items if after is None or name_of(item) > after]
    page = remaining[:_PAGE_SIZE]
    next_cursor = _encode_cursor(name_of(page[-1])) if len(remaining) > _PAGE_SIZE and page else None
    return page, next_cursor


class MCPServerCore:
    """Expose catalog entries through the MCP protocol result types."""

    def __init__(self, catalog: Catalog, request: typing.Any = None, proxy: RestProxy | None = None) -> None:
        self.catalog = catalog
        self.request = request
        self.proxy = proxy if proxy is not None else RestProxy(request=request)
        if request is not None:
            self.proxy.request = request

    async def list_tools(
        self,
        _context: typing.Any,
        params: mcp.types.PaginatedRequestParams | None,
    ) -> mcp.types.ListToolsResult:
        """Return one page of the MCP tool list generated from the catalog."""
        tools, next_cursor = _paginate(list(self.catalog.tools()), params.cursor if params else None, lambda t: t.name)
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
                for tool in tools
            ],
            next_cursor=next_cursor,
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

        # Validate against the published input schema before touching the
        # REST proxy, so argument mistakes fail fast with a precise
        # ``invalid params`` message.
        validate_arguments(tool.input_schema, params.arguments or {})

        # The caller (``MCPHandler``) is responsible for binding the live
        # HTTP request before invoking us. The proxy helpers will fail
        # cleanly with a request-related error if it is missing.
        result = await tool.executor(params.arguments or {}, self.request)
        safe_result = redact(_json_safe(result))
        return mcp.types.CallToolResult(
            content=[mcp.types.TextContent(text=json.dumps(safe_result, default=str))],
            structured_content=safe_result,
        )

    async def list_resources(
        self,
        _context: typing.Any,
        params: mcp.types.PaginatedRequestParams | None,
    ) -> mcp.types.ListResourcesResult:
        """Return one page of the MCP resource list generated from the catalog."""
        resources, next_cursor = _paginate(
            list(self.catalog.resources()), params.cursor if params else None, lambda r: r.uri
        )
        return mcp.types.ListResourcesResult(
            resources=[
                mcp.types.Resource(
                    uri=resource.uri,
                    name=resource.name,
                    title=resource.title,
                    description=resource.description,
                )
                for resource in resources
            ],
            next_cursor=next_cursor,
        )

    async def read_resource(
        self,
        _context: typing.Any,
        params: mcp.types.ReadResourceRequestParams,
    ) -> mcp.types.ReadResourceResult:
        """Read a catalog resource and redact its contents."""
        resource = self.catalog.get_resource(params.uri)
        if resource is None or (resource.reader is None and resource.target is None):
            raise ValueError(f"Unknown MCP resource: {params.uri}")

        if resource.target is not None:
            if self.request is None:
                raise RuntimeError("MCP resource target requires an HTTP request")
            content = await self.proxy.execute(resource.target, self.request, {})
        else:
            reader = typing.cast(
                collections.abc.Callable[[str], collections.abc.Awaitable[typing.Any]], resource.reader
            )
            content = await reader(params.uri)
        return mcp.types.ReadResourceResult(
            contents=typing.cast(
                list[mcp.types.TextResourceContents | mcp.types.BlobResourceContents],
                [
                    mcp.types.TextResourceContents(
                        uri=params.uri,
                        mime_type="text/plain",
                        text=json.dumps(redact(_json_safe(content)), default=str),
                    )
                ],
            )
        )
