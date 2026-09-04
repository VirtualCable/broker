"""Service pool analysis tools: fallback access, forecast and cache sizing."""

from uds.REST.methods.meta_pools import MetaPools
from uds.REST.methods.services_pools import ServicesPools

from ..catalog import ToolDefinition
from .helpers import master_custom_tool, string_property, uuid_property

__all__ = ["curated_tools"]


def curated_tools() -> tuple[ToolDefinition, ...]:
    """Return the service pool and meta pool analysis tools."""
    return (
        master_custom_tool(
            name="get_servicepool_fallback_access",
            title="Get service pool fallback access",
            description="Current fallback access policy of a service pool (what happens when no calendar rule applies).",
            handler=ServicesPools,
            path="servicespools",
            custom_name="fallback_access",
            uuid_property=uuid_property("UUID of the service pool."),
            access="Available to authenticated UDS users with read permission on the pool.",
            returns="The pool's fallback access policy.",
        ),
        master_custom_tool(
            name="get_metapool_fallback_access",
            title="Get meta pool fallback access",
            description="Current fallback access policy of a meta pool (what happens when no calendar rule applies).",
            handler=MetaPools,
            path="metapools",
            custom_name="fallback_access",
            uuid_property=uuid_property("UUID of the meta pool."),
            access="Available to authenticated UDS users with read permission on the meta pool.",
            returns="The meta pool's fallback access policy.",
        ),
        master_custom_tool(
            name="get_servicepool_forecast",
            title="Get service pool usage forecast",
            description="Forecast future usage of a service pool from its historical weekly profile.",
            handler=ServicesPools,
            path="servicespools",
            custom_name="forecast",
            uuid_property=uuid_property("UUID of the service pool."),
            extra_properties={
                "counter": string_property("Counter to forecast (inuse, assigned or cached). Default: inuse."),
                "hours": {
                    "type": "integer",
                    "description": "Hours to forecast, from 1 to 168 (a week). Default: 72.",
                    "minimum": 1,
                    "maximum": 168,
                },
            },
            access="Available to authenticated UDS users with read permission on the pool.",
            returns="Forecast points (p50/p75/p90/max per hour) with confidence and sample counts.",
        ),
        master_custom_tool(
            name="get_servicepool_cache_recommendations",
            title="Get service pool cache recommendations",
            description="Cache sizing recommendations for a service pool based on predicted usage patterns.",
            handler=ServicesPools,
            path="servicespools",
            custom_name="cache_recommendations",
            uuid_property=uuid_property("UUID of the service pool."),
            access="Available to authenticated UDS users with read permission on the pool.",
            returns="Recommended cache configuration with the current one and per-level verdicts.",
        ),
    )
