"""Server fleet tools: group aggregates and per-server usage series."""

import typing

from asgiref.sync import sync_to_async

from uds.REST.methods.servers_management import ServersGroups, ServersServers

from ..catalog import ToolDefinition
from ..rest_proxy import RestProxy, RestTarget
from .helpers import GET, JsonObject, check_required, master_custom_tool, schema, uuid_property

__all__ = ["curated_tools"]


def _server_stats_tool() -> ToolDefinition:
    """Build the per-server resource usage tool (a detail custom method).

    The target is a ``DetailHandler`` custom method under
    ``servers/groups``, so the executor resolves the parent group (its uuid
    carries the permission check) and then dispatches the detail handler
    with ``(server_uuid, "stats")`` as URL arguments.
    """

    target_parent = RestTarget(ServersGroups, "servers/groups")

    async def executor(arguments: JsonObject, request: typing.Any = None) -> typing.Any:
        check_required(arguments, ("group_uuid", "server_uuid"))
        params = {key: value for key, value in arguments.items() if key not in ("group_uuid", "server_uuid")}
        target = RestTarget(
            ServersServers,
            "servers/groups/{uuid}/servers",
            GET,
            args=(str(arguments["server_uuid"]), "stats"),
            parent=target_parent,
        )
        return await sync_to_async(RestProxy._execute_sync, thread_sensitive=True)(
            target, request, params, str(arguments["group_uuid"])
        )

    return ToolDefinition(
        name="get_server_stats",
        title="Get server stats",
        description=(
            "Accumulated resource usage time-series for one server of a server group "
            "(cpu, memory, users, connections, disk)."
        ),
        input_schema=schema(
            {
                "group_uuid": uuid_property("UUID of the server group the server belongs to."),
                "server_uuid": uuid_property("UUID of the server (list them with ``list_servers_servers``)."),
                "counter": {
                    "type": "string",
                    "description": "Counter to retrieve (all, cpu, memory, users, connections, disk). Default: all.",
                },
                "interval": {"type": "string", "description": "Accumulation interval (hour or day). Default: hour."},
                "since": {"type": "integer", "description": "Number of days to go back. Default: 14."},
            },
            ("group_uuid", "server_uuid"),
        ),
        access="Available to authenticated UDS users with read permission on the server group.",
        returns="A dictionary with the requested usage time-series.",
        required_permission="READ",
        executor=executor,
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
        _server_stats_tool(),
    )
