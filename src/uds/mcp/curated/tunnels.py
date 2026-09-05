"""Tunnel tools: per-tunnel-group inventory of unassigned tunnels."""

from uds.REST.methods.tunnels_management import Tunnels

from ..catalog import ToolDefinition
from .helpers import master_custom_tool, uuid_property

__all__ = ["curated_tools"]


def curated_tools() -> tuple[ToolDefinition, ...]:
    """Return the tunnel tools."""
    return (
        master_custom_tool(
            name="get_tunnel_group_unassigned_tunnels",
            title="Get tunnel group's unassigned tunnels",
            description=(
                "List the tunnel servers in a tunnel group that are not yet "
                "assigned to this group. Used to estimate spare capacity or "
                "to find candidates when adding tunnels."
            ),
            handler=Tunnels,
            path="tunnels",
            custom_name="tunnels",
            uuid_property=uuid_property("UUID of the tunnel group."),
            access="Available to users with management permission on the tunnel group.",
            returns="An array of tunnel server items not yet assigned to the group.",
            required_permission="ALL",
        ),
    )
