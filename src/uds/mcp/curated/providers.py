"""Provider tools: cross-provider lookups and per-service relations."""

from uds.REST.methods.providers import Providers

from ..catalog import ToolDefinition
from .helpers import master_custom_tool, nested_custom_tool, uuid_property

__all__ = ["curated_tools"]


def curated_tools() -> tuple[ToolDefinition, ...]:
    """Return the provider tools."""
    return (
        master_custom_tool(
            name="get_provider_allservices",
            title="List all services across providers",
            description=(
                "List every service in the catalog, across all providers and "
                "filtered to the ones the caller can read. Useful when the "
                "agent does not know which provider owns a service or wants "
                "a flat view of the whole catalog."
            ),
            handler=Providers,
            path="providers",
            custom_name="allservices",
            path_args=(),
            access="Available to authenticated UDS users; only services whose provider is readable are included.",
            returns="An array of service items, each as a dictionary.",
        ),
        master_custom_tool(
            name="get_provider_service",
            title="Get a service by UUID",
            description=(
                "Retrieve a specific service by its UUID without needing to "
                'know its provider. This is the "I know the service UUID, '
                'give me its metadata" entry point.'
            ),
            handler=Providers,
            path="providers",
            custom_name="service",
            path_args=("item_id",),
            method_first=True,
            extra_arg_properties={"item_id": uuid_property("UUID of the service to look up.")},
            access="Available to authenticated UDS users with read permission on the service's provider.",
            returns="The service item as a dictionary, or an empty object if not found.",
        ),
        nested_custom_tool(
            name="get_provider_service_servicepools",
            title="Get a service's service pools",
            description=(
                "List every service pool that references this service. "
                "Multiple pools can share one service (cache levels, "
                "metapool members, ...); this call returns them all."
            ),
            handler=Providers,
            path="providers",
            intermediate_name="services",
            custom_name="servicepools",
            uuid_property=uuid_property("UUID of the provider that owns the service."),
            item_property=uuid_property("UUID of the service inside that provider."),
            access="Available to users with read permission on the provider.",
            returns="An array of service pools that reference the service.",
        ),
    )
