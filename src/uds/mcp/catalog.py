"""Declarative catalog for the UDS MCP surface."""

import dataclasses
import typing
import collections.abc

from .rest_proxy import RestTarget


ToolExecutor: typing.TypeAlias = collections.abc.Callable[
    [dict[str, typing.Any]], collections.abc.Awaitable[typing.Any]
]
ResourceReader: typing.TypeAlias = collections.abc.Callable[[str], collections.abc.Awaitable[typing.Any]]


@dataclasses.dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Describe an MCP tool independently from its transport implementation."""

    name: str
    title: str
    description: str
    input_schema: dict[str, typing.Any]
    access: str
    returns: str
    required_permission: str | None = None
    read_only: bool = True
    sensitive_fields: tuple[str, ...] = ()
    executor: ToolExecutor | None = dataclasses.field(default=None, repr=False, compare=False)


@dataclasses.dataclass(frozen=True, slots=True)
class ResourceDefinition:
    """Describe an MCP resource exposed by the UDS catalog."""

    uri: str
    name: str
    title: str
    description: str
    access: str
    returns: str
    required_permission: str | None = None
    sensitive_fields: tuple[str, ...] = ()
    reader: ResourceReader | None = dataclasses.field(default=None, repr=False, compare=False)
    target: RestTarget | None = dataclasses.field(default=None, repr=False, compare=False)


class Catalog:
    """Store the curated tools and resources exposed by the MCP server."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._resources: dict[str, ResourceDefinition] = {}

    def add_tool(self, tool: ToolDefinition) -> None:
        """Add a tool, rejecting duplicate names."""
        if tool.name in self._tools:
            raise ValueError(f"MCP tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def add_resource(self, resource: ResourceDefinition) -> None:
        """Add a resource, rejecting duplicate URIs."""
        if resource.uri in self._resources:
            raise ValueError(f"MCP resource already registered: {resource.uri}")
        self._resources[resource.uri] = resource

    def get_tool(self, name: str) -> ToolDefinition | None:
        """Return a registered tool by name."""
        return self._tools.get(name)

    def get_resource(self, uri: str) -> ResourceDefinition | None:
        """Return a registered resource by URI."""
        return self._resources.get(uri)

    def tools(self) -> collections.abc.Iterable[ToolDefinition]:
        """Return tools in stable name order."""
        return tuple(self._tools[name] for name in sorted(self._tools))

    def resources(self) -> collections.abc.Iterable[ResourceDefinition]:
        """Return resources in stable URI order."""
        return tuple(self._resources[uri] for uri in sorted(self._resources))
