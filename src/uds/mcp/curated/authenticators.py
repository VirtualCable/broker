"""Authenticator tools: user/group search."""

from uds.REST.methods.authenticators import Authenticators

from ..catalog import ToolDefinition
from .helpers import master_custom_tool, string_property, uuid_property

__all__ = ["curated_tools"]


def curated_tools() -> tuple[ToolDefinition, ...]:
    """Return the authenticator tools."""
    return (
        master_custom_tool(
            name="search_authenticator",
            title="Search authenticator users or groups",
            description="Search users or groups of an authenticator by name or identifier.",
            handler=Authenticators,
            path="authenticators",
            custom_name="search",
            uuid_property=uuid_property("UUID of the authenticator to search in."),
            extra_properties={
                "type": string_property("What to search for: user or group."),
                "term": string_property("Search text to match against user or group names."),
                "limit": {"type": "integer", "description": "Maximum number of results. Default: 50.", "minimum": 1},
            },
            extra_required=("type", "term"),
            access="Available to authenticated UDS users with read permission on the authenticator.",
            returns="An array of matching users or groups as reported by the authenticator.",
        ),
    )
