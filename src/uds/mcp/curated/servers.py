"""Server fleet tools: group aggregates, per-server usage series and forecasts."""

import typing

from asgiref.sync import sync_to_async

from uds.REST.methods.servers_management import ServersGroups, ServersServers
from uds.core.types.requests import ExtendedHttpRequestWithUser

from ..catalog import ToolDefinition
from ..rest_proxy import RestProxy, RestTarget
from .helpers import (
    GET,
    POST,
    JsonObject,
    check_required,
    master_custom_tool,
    nested_custom_tool,
    schema,
    uuid_property,
)

__all__ = ["curated_tools"]


def _server_detail_tool(
    *,
    name: str,
    title: str,
    description: str,
    method_name: str,
    returns: str,
    extra_props: JsonObject | None = None,
) -> ToolDefinition:
    """Build a tool around a GET detail custom method of a group's server.

    Shared wiring for the per-server reads (``stats``, ``forecast``): the
    target is a ``DetailHandler`` custom method under ``servers/groups``, so
    the executor resolves the parent group (its uuid carries the permission
    check) and then dispatches the detail handler with ``(server_uuid,
    method_name)`` as URL arguments.
    """

    target_parent = RestTarget(ServersGroups, "servers/groups")

    async def executor(arguments: JsonObject, request: ExtendedHttpRequestWithUser | None = None) -> typing.Any:
        check_required(arguments, ("group_uuid", "server_uuid"))
        params = {key: value for key, value in arguments.items() if key not in ("group_uuid", "server_uuid")}
        target = RestTarget(
            ServersServers,
            "servers/groups/{uuid}/servers",
            GET,
            args=(str(arguments["server_uuid"]), method_name),
            parent=target_parent,
        )
        return await sync_to_async(RestProxy._execute_sync, thread_sensitive=True)(
            target, RestProxy._bound(request), params, str(arguments["group_uuid"])
        )

    return ToolDefinition(
        name=name,
        title=title,
        description=description,
        input_schema=schema(
            {
                "group_uuid": uuid_property("UUID of the server group the server belongs to."),
                "server_uuid": uuid_property("UUID of the server (list them with ``list_servers_servers``)."),
                **(extra_props or {}),
            },
            ("group_uuid", "server_uuid"),
        ),
        access="Available to authenticated UDS users with read permission on the server group.",
        returns=returns,
        required_permission="READ",
        executor=executor,
    )


def _server_stats_tool() -> ToolDefinition:
    return _server_detail_tool(
        name="get_server_stats",
        title="Get server stats",
        description=(
            "Accumulated resource usage time-series for one server of a server group "
            "(cpu, memory, users, connections, disk)."
        ),
        method_name="stats",
        extra_props={
            "counter": {
                "type": "string",
                "description": "Counter to retrieve (all, cpu, memory, users, connections, disk). Default: all.",
            },
            "interval": {
                "type": "string",
                "description": "Accumulation interval (hour or day). Default: hour.",
            },
            "since": {"type": "integer", "description": "Number of days to go back. Default: 14."},
        },
        returns="A dictionary with the requested usage time-series.",
    )


def _server_forecast_tool() -> ToolDefinition:
    return _server_detail_tool(
        name="get_server_forecast",
        title="Get server saturation forecast",
        description=(
            "Saturation forecast of one managed server: per-metric status, fitted growth, "
            "days until each configured threshold is crossed, and hourly forecast points. "
            "The primary metric is the composite load (cpu/memory/users weighted by the "
            "server group); disk, users and connections are secondary and only reported "
            "when their threshold is enabled. Answers 'is this server heading to "
            "saturation, and when?'; pair with ``get_server_stats`` for the past side."
        ),
        method_name="forecast",
        extra_props={
            "hours": {
                "type": "integer",
                "description": "Hours of hourly forecast points to return, from 1 to 168 (a week). Default: 72.",
                "minimum": 1,
                "maximum": 168,
            },
            "metric": {
                "type": "string",
                "description": "Restrict to one metric (load, disk, users, connections). Default: all enabled ones.",
            },
        },
        returns=(
            "Server status, saturating_in_days, effective thresholds and weights, one row "
            "per metric (status, current p90, growth fit, days and date of saturation) and "
            "the hourly forecast points flagged against each threshold."
        ),
    )


def curated_tools() -> tuple[ToolDefinition, ...]:
    """Return the server fleet tools."""
    return (
        master_custom_tool(
            name="get_server_group_stats",
            title="Get server group stats",
            description="Aggregate statistics of a server group: per-server status, load and weights.",
            handler=ServersGroups,
            path="servers/groups",
            custom_name="stats",
            uuid_property=uuid_property("UUID of the server group."),
            access="Available to authenticated UDS users with read permission on the group.",
            returns="Per-server statistics for the group.",
        ),
        master_custom_tool(
            name="get_server_group_forecast",
            title="Get server group saturation forecast",
            description=(
                "Saturation forecast rollup of a server group: per-server status and days "
                "until each crosses its configured thresholds, collapsed into the group "
                "verdict. With the internal load balancer the first server to saturate is "
                "the honest collective signal, so ``min_days_to_saturation`` answers "
                "'when does this group need another server?'. A wide spread across "
                "``per_server`` means balancing is not distributing evenly."
            ),
            handler=ServersGroups,
            path="servers/groups",
            custom_name="forecast",
            uuid_property=uuid_property("UUID of the server group."),
            extra_properties={
                "hours": {
                    "type": "integer",
                    "description": "Hours of hourly forecast points behind the per-server data, 1 to 168. Default: 72.",
                    "minimum": 1,
                    "maximum": 168,
                },
            },
            access="Available to authenticated UDS users with read permission on the group.",
            returns=(
                "Group status, worst server, minimum days to saturation and a per-server "
                "list with id, label, status and saturating_in_days."
            ),
        ),
        master_custom_tool(
            name="get_server_group_usages",
            title="Get server group usages",
            description=(
                "Providers and services that reference a server group in their "
                "configuration (the same scan the administration interface shows "
                "before allowing the group to be deleted). Use it to find who "
                "depends on a group, and as the pre-check before deleting it: a "
                "group still in use cannot be deleted."
            ),
            handler=ServersGroups,
            path="servers/groups",
            custom_name="usages",
            uuid_property=uuid_property("UUID of the server group."),
            access="Available to authenticated UDS users with read permission on the group.",
            returns="An array of referencing items, each with uuid, name, type and kind (provider or service).",
        ),
        _server_stats_tool(),
        _server_forecast_tool(),
        nested_custom_tool(
            name="set_server_maintenance",
            title="Toggle server maintenance",
            description=(
                "Toggle the maintenance mode of one server of a server group (enable if "
                "disabled, disable if enabled). A server in maintenance stops receiving "
                "new user service assignments."
            ),
            handler=ServersGroups,
            path="servers/groups",
            intermediate_name="servers",
            custom_name="maintenance",
            uuid_property=uuid_property("UUID of the server group the server belongs to."),
            item_property=uuid_property("UUID of the server inside that group."),
            method=POST,
            access="Available to authenticated UDS users with management permission on the server group.",
            returns="``ok`` when the toggle is applied.",
            required_permission="MANAGEMENT",
        ),
    )
