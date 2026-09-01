"""Curated read-only MCP catalog backed by existing REST handlers."""

import typing

from uds.REST.methods.system import System
from uds.REST.methods.version import UDSVersion

from .catalog import Catalog, ResourceDefinition, ToolDefinition
from .rest_proxy import ODataArgs, RestTarget, RestProxy
from .tools import _list_input_schema, generated_list_tools


_SERVICE_POOLS_DESCRIPTION = (
    "List UDS service pools. Supports OData-style filtering, ordering, "
    "pagination and projection through the structured arguments below. "
    "This tool is curated explicitly to expose the service-pool access "
    "semantics that the generic generator cannot infer."
)
_SERVICE_POOLS_ACCESS = "Available to authenticated UDS users with permission to read service pools."
_SERVICE_POOLS_RETURNS = (
    "An array of service pool summaries, each as a dictionary, with the "
    "fields selected by the client or the REST default projection."
)


def build_catalog() -> Catalog:
    """Build the initial read-only MCP catalog from REST handlers."""
    catalog = Catalog()

    # 1. Curated resources (read-only, no executor needed).
    resources = (
        ResourceDefinition(
            uri="uds://system/overview",
            name="system-overview",
            title="System overview",
            description="Aggregate counts and status information for the UDS platform.",
            access="Available to authenticated administrators through the REST system overview permission.",
            returns="Bounded aggregate platform counters.",
            target=RestTarget(System, "system", args=("overview",)),
        ),
        ResourceDefinition(
            uri="uds://version",
            name="version",
            title="UDS version",
            description="The running UDS version and build information.",
            access="Available to an authenticated UDS user.",
            returns="The UDS version and build metadata.",
            target=RestTarget(UDSVersion, "version"),
        ),
    )
    for resource in resources:
        catalog.add_resource(resource)

    # 2. Curated service-pool list tool (rich description).
    from uds.REST.methods.services_pools import ServicesPools

    async def list_service_pools_executor(
        arguments: dict[str, typing.Any],
    ) -> list[typing.Any]:
        proxy = RestProxy()
        args = ODataArgs(
            filter=typing.cast("str | None", arguments.get("filter")),
            orderby=typing.cast("str | None", arguments.get("orderby")),
            top=typing.cast("int | None", arguments.get("top")),
            skip=typing.cast("int | None", arguments.get("skip")),
            select=tuple(typing.cast("list[str]", arguments.get("select", []) or [])),
        )
        return await proxy.execute_collection(
            RestTarget(ServicesPools, "servicespools"),
            None,
            args,
        )

    catalog.add_tool(
        ToolDefinition(
            name="list_service_pools",
            title="List service pools",
            description=_SERVICE_POOLS_DESCRIPTION,
            input_schema=_list_input_schema(),
            access=_SERVICE_POOLS_ACCESS,
            returns=_SERVICE_POOLS_RETURNS,
            required_permission="READ",
            executor=list_service_pools_executor,
        )
    )

    # 3. Generic list tools for every other collection handler.
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
    from .rest_proxy import RestTarget

    async def bound(arguments: dict[str, typing.Any]) -> typing.Any:
        proxy = RestProxy()
        proxy.request = request
        # Rebuild the call so we route through the bound proxy. The
        # tools the generator produces close over a ``(ServicesPools,
        # "servicespools")`` pair; we recover the (handler, path) pair
        # from the closure's captured variables.
        handler_cls = getattr(executor, "_uds_handler_class", None)
        path = getattr(executor, "_uds_handler_path", None)
        if handler_cls is None or path is None:
            return await executor(arguments)
        target = RestTarget(handler_cls, path)
        args = ODataArgs(
            filter=arguments.get("filter"),
            orderby=arguments.get("orderby"),
            top=arguments.get("top"),
            skip=arguments.get("skip"),
            select=tuple(arguments.get("select") or ()),
        )
        return await proxy.execute_collection(target, request, args)

    return bound
