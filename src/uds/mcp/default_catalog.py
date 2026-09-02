"""Curated read-only MCP catalog backed by existing REST handlers.

The catalog is built from the same :mod:`uds.REST.inventory` walker that
``genapi`` uses, so resources and tools stay aligned with the REST surface.
The curated pieces here are only the *descriptions* that the generic
generator cannot infer (system overview, version, service pools); the
capabilities themselves come from the walker.
"""

import typing

from uds.REST.inventory import (
    HandlerInventoryEntry,
    walk_rest_handlers,
)
from uds.REST.methods.system import System
from uds.REST.methods.version import UDSVersion

from .catalog import Catalog, ResourceDefinition, ToolDefinition
from .rest_proxy import RestProxy, RestTarget
from .tools import generated_list_tools


def _curated_resource(entry: HandlerInventoryEntry) -> ResourceDefinition | None:
    """Return the curated ``ResourceDefinition`` for the inventory entry, if any."""
    if entry.name == "system":
        return ResourceDefinition(
            uri="uds://system/overview",
            name="system-overview",
            title="System overview",
            description="Aggregate counts and status information for the UDS platform.",
            access=("Available to authenticated administrators through the REST system overview permission."),
            returns="Bounded aggregate platform counters.",
            target=RestTarget(System, "system", args=("overview",)),
        )
    if entry.name == "version":
        return ResourceDefinition(
            uri="uds://version",
            name="version",
            title="UDS version",
            description="The running UDS version and build information.",
            access="Available to an authenticated UDS user.",
            returns="The UDS version and build metadata.",
            target=RestTarget(UDSVersion, "version"),
        )
    return None


def _curated_resources(catalog: Catalog) -> None:
    """Register the curated resources from the walker's real tree nodes.

    ``System`` and ``UDSVersion`` are registered handlers, so they appear in
    :func:`walk_rest_handlers` as ordinary entries. We deduplicate by
    resource name so two handlers with the same ``NAME`` do not both produce
    a ``uds://version`` registration.
    """
    seen_names: set[str] = set()
    for entry in walk_rest_handlers():
        curated = _curated_resource(entry)
        if curated is None:
            continue
        if curated.name in seen_names:
            continue
        seen_names.add(curated.name)
        catalog.add_resource(curated)


def build_catalog() -> Catalog:
    """Build the initial read-only MCP catalog from REST handlers.

    The catalog is built from the same inventory the generator and
    ``genapi`` use, so the resources and tools stay aligned with the REST
    surface.
    """
    catalog = Catalog()

    _curated_resources(catalog)

    # Generic list tools for every collection handler (master and detail).
    # The generator derives names from the handler's full path, so service
    # pools surface as ``list_servicespools`` like every other collection.
    for tool in generated_list_tools():
        catalog.add_tool(tool)

    return catalog


def default_catalog_for_request(request: typing.Any) -> Catalog:
    """Build a catalog whose tools receive ``request`` through their executor.

    The MCP handler passes the live HTTP request to ``MCPServerCore`` so
    the REST proxy can re-authenticate and re-authorise each call. Each
    generated tool closes over that request and uses it for every list
    call.
    """
    catalog = build_catalog()

    rebuilt: list[ToolDefinition] = []
    for tool in catalog.tools():
        rebuilt.append(
            ToolDefinition(
                name=tool.name,
                title=tool.title,
                description=tool.description,
                input_schema=tool.input_schema,
                access=tool.access,
                returns=tool.returns,
                required_permission=tool.required_permission,
                read_only=tool.read_only,
                sensitive_fields=tool.sensitive_fields,
                executor=_wrap_with_request(tool.executor, request),
            )
        )

    fresh = Catalog()
    for resource in catalog.resources():
        fresh.add_resource(resource)
    for tool in rebuilt:
        fresh.add_tool(tool)
    return fresh


def _wrap_with_request(
    executor: typing.Any,
    request: typing.Any,
) -> typing.Any:
    """Bind a tool's ``executor`` so it sees the live HTTP request.

    The generator writes a closure that creates a fresh ``RestProxy`` for
    every call, but each call must use the request that authenticated
    the MCP user. This wrapper patches the proxy that the closure
    creates so the right request reaches the REST handler.
    """

    async def bound(arguments: dict[str, typing.Any]) -> typing.Any:
        proxy = RestProxy()
        proxy.request = request
        # Rebuild the call so we route through the bound proxy. The
        # tools the generator produces close over a ``(handler, path)``
        # pair; we recover the (handler, path, parent) pair from the
        # closure's captured variables.
        handler_cls = getattr(executor, "_uds_handler_class", None)
        path = getattr(executor, "_uds_handler_path", None)
        if handler_cls is None or path is None:
            return await executor(arguments)
        parent_cls = getattr(executor, "_uds_parent_class", None)
        parent_path = getattr(executor, "_uds_parent_path", None)
        parent_target = (
            RestTarget(parent_cls, parent_path) if parent_cls is not None and parent_path is not None else None
        )
        target = RestTarget(handler_cls, path, parent=parent_target)
        return await proxy.execute_collection(target, request, arguments)

    return bound
