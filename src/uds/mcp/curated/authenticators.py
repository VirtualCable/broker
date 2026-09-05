"""Authenticator tools: user/group search and per-user/group relations."""

from uds.REST.methods.authenticators import Authenticators

from ..catalog import ToolDefinition
from .helpers import (
    master_custom_tool,
    nested_custom_tool,
    string_property,
    uuid_property,
)

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
        master_custom_tool(
            name="get_authenticator_users_with_services",
            title="Get authenticator users with services",
            description=(
                "List all users in this authenticator that currently have at "
                "least one assigned (deployed) service. Useful to triage "
                "authenticator incidents or to estimate how many users are "
                "actually consuming the platform."
            ),
            handler=Authenticators,
            path="authenticators",
            custom_name="users_with_services",
            uuid_property=uuid_property("UUID of the authenticator."),
            access="Available to users with management permission on the authenticator.",
            returns="An array of users (with active services attached) as reported by the authenticator.",
            required_permission="ALL",
        ),
        nested_custom_tool(
            name="get_authenticator_user_services_pools",
            title="Get user service pools",
            description=(
                "Service pools in which this user has active assignments. "
                "Each entry describes the pool and the live deployment count "
                'for that user, so a single call answers "which pools does '
                'this user actually use?".'
            ),
            handler=Authenticators,
            path="authenticators",
            intermediate_name="users",
            custom_name="services_pools",
            uuid_property=uuid_property("UUID of the authenticator the user belongs to."),
            item_property=uuid_property("Id or UUID of the user inside that authenticator."),
            access="Available to users with management permission on the authenticator.",
            returns="An array of service pools the user has active assignments on.",
        ),
        nested_custom_tool(
            name="get_authenticator_user_user_services",
            title="Get user user services",
            description=(
                "List the user services currently assigned to this user. "
                "These are the live (deployed) services the user can connect "
                "to; the corresponding pool and state are returned per entry."
            ),
            handler=Authenticators,
            path="authenticators",
            intermediate_name="users",
            custom_name="user_services",
            uuid_property=uuid_property("UUID of the authenticator the user belongs to."),
            item_property=uuid_property("Id or UUID of the user inside that authenticator."),
            access="Available to users with management permission on the authenticator.",
            returns="An array of deployed user services belonging to the user.",
        ),
        nested_custom_tool(
            name="get_authenticator_group_services_pools",
            title="Get group service pools",
            description=(
                "Service pools this group has access to. Membership in a "
                'group grants pool access, so this answers "what can a user '
                'in this group reach?" without enumerating individual members.'
            ),
            handler=Authenticators,
            path="authenticators",
            intermediate_name="groups",
            custom_name="services_pools",
            uuid_property=uuid_property("UUID of the authenticator the group belongs to."),
            item_property=uuid_property("Id or UUID of the group inside that authenticator."),
            access="Available to users with management permission on the authenticator.",
            returns="An array of service pools the group has access to.",
        ),
        nested_custom_tool(
            name="get_authenticator_group_users",
            title="Get group members",
            description=(
                "Users belonging to this group. Combined with the "
                "``services_pools`` read of the same group, this is enough "
                "to audit who is reached by a pool and how."
            ),
            handler=Authenticators,
            path="authenticators",
            intermediate_name="groups",
            custom_name="users",
            uuid_property=uuid_property("UUID of the authenticator the group belongs to."),
            item_property=uuid_property("Id or UUID of the group inside that authenticator."),
            access="Available to users with management permission on the authenticator.",
            returns="An array of users that belong to the group.",
        ),
    )
