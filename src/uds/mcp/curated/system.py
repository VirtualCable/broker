"""Platform tools: usage counters and the security self-assessment."""

import typing

from uds.REST.methods.system import System

from ..catalog import ToolDefinition
from ..rest_proxy import RestProxy, RestTarget
from .helpers import GET, JsonObject, schema, string_property, uuid_property

__all__ = ["curated_tools"]

_COUNTERS: typing.Final[tuple[str, ...]] = ("assigned", "inuse", "cached", "complete")


def _platform_stats_tool() -> ToolDefinition:
    """Build the platform-wide usage counters tool (``/system/stats``)."""

    async def executor(arguments: JsonObject, request: typing.Any = None) -> typing.Any:
        counter = str(arguments.get("counter", "")).lower()
        if counter not in _COUNTERS:
            raise ValueError(f"counter must be one of {', '.join(_COUNTERS)}")
        args = ["stats", counter]
        pool_uuid = arguments.get("pool_uuid")
        if pool_uuid:
            args.append(str(pool_uuid))
        return await RestProxy().execute(RestTarget(System, "system", GET, args=tuple(args)), request, {})

    return ToolDefinition(
        name="get_platform_stats",
        title="Get platform usage stats",
        description=(
            "Historical usage series of the platform: assigned, in use, cached or complete "
            "(the three at once) services. Without ``pool_uuid`` the series cover every pool "
            "of the platform (administrators only); with it, the series of that single pool "
            "(available to staff with read permission on it). Use it to decide capacity "
            "changes; pair with ``get_servicepool_forecast`` for the future side."
        ),
        input_schema=schema(
            {
                "counter": string_property("Which series: assigned, inuse, cached or complete."),
                "pool_uuid": uuid_property(
                    "Optional UUID of a service pool to scope the series to. "
                    "Omit for platform-wide (administrators only)."
                ),
            },
            ("counter",),
        ),
        access="Platform-wide series: administrators only. Per-pool series: staff with read permission on the pool.",
        returns="An array of counter samples (or an object with the three series for complete).",
        required_permission="READ",
        executor=executor,
    )


def _security_check_tool() -> ToolDefinition:
    """Build the security self-assessment tool (``/system/security_check``)."""

    async def executor(arguments: JsonObject, request: typing.Any = None) -> typing.Any:
        return await RestProxy().execute(RestTarget(System, "system", GET, args=("security_check",)), request, {})

    return ToolDefinition(
        name="get_security_check",
        title="Get security check",
        description=(
            "Security self-assessment of this broker: configuration findings that an "
            "administrator should review (weak settings, insecure defaults, ...). "
            "Administrators only; staff get an access-denied error."
        ),
        input_schema=schema({}),
        access="Administrators only.",
        returns="An object with the security findings and their severity.",
        required_permission="ALL",
        executor=executor,
    )


def curated_tools() -> tuple[ToolDefinition, ...]:
    """Return the platform tools."""
    return (
        _platform_stats_tool(),
        _security_check_tool(),
    )
