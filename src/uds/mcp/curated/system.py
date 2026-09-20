"""Platform tools: usage counters, the security self-assessment, the
diagnostics dashboard and the global configuration."""

import typing

from uds.REST.methods.config import Config as ConfigHandler
from uds.REST.methods.dashboard import Dashboard
from uds.REST.methods.system import System
from uds.core.types.requests import ExtendedHttpRequestWithUser

from ..catalog import ToolDefinition
from ..rest_proxy import RestProxy, RestTarget
from .helpers import GET, JsonObject, schema, string_property, uuid_property

__all__ = ["curated_tools"]

_COUNTERS: typing.Final[tuple[str, ...]] = ("assigned", "inuse", "cached", "complete")

#: Widgets of the dashboard payload, as the REST builder names them. The
#: agent can ask for a single one to keep the answer small.
_DASHBOARD_WIDGETS: typing.Final[tuple[str, ...]] = (
    "kpis",
    "peak_concurrency",
    "pool_saturation",
    "cache_efficiency",
    "tunnel_usage",
    "client_platforms",
    "top_users",
    "session_duration",
    "userservice_errors",
    "failed_logins",
)


def _platform_stats_tool() -> ToolDefinition:
    """Build the platform-wide usage counters tool (``/system/stats``)."""

    async def executor(arguments: JsonObject, request: ExtendedHttpRequestWithUser | None = None) -> typing.Any:
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

    async def executor(arguments: JsonObject, request: ExtendedHttpRequestWithUser | None = None) -> typing.Any:
        return await RestProxy().execute(
            RestTarget(System, "system", GET, args=("security_check",)), request, {}
        )

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


def _config_tool() -> ToolDefinition:
    """Build the global configuration read tool (``GET /config``)."""

    async def executor(arguments: JsonObject, request: ExtendedHttpRequestWithUser | None = None) -> typing.Any:
        return await RestProxy().execute(RestTarget(ConfigHandler, "config", GET), request, {})

    return ToolDefinition(
        name="get_config",
        title="Get global configuration",
        description=(
            "Current UDS global configuration, grouped by section (Security, UDS, Custom, ...). "
            "Secret values (passwords, hidden entries) are masked and never travel. "
            "Administrators only. Use get_security_check to see which settings need attention, "
            "and the config.update proposal flow to suggest changes."
        ),
        input_schema=schema({}),
        access="Administrators only; staff get an access-denied error.",
        returns="An object keyed by section, each holding its configuration values (value, type, params, help).",
        required_permission="ALL",
        executor=executor,
    )


def _dashboard_tool() -> ToolDefinition:
    """Build the diagnostics dashboard tool (``GET /dashboard/data``)."""

    async def executor(arguments: JsonObject, request: ExtendedHttpRequestWithUser | None = None) -> typing.Any:
        widget = str(arguments.get("widget", "")).strip().lower() or None
        if widget is not None and widget not in _DASHBOARD_WIDGETS:
            raise ValueError(f"widget must be one of {', '.join(_DASHBOARD_WIDGETS)}")
        params: JsonObject = {}
        if arguments.get("days") is not None:
            params["days"] = str(int(arguments["days"]))
        data = await RestProxy().execute(
            RestTarget(Dashboard, "dashboard", GET, args=("data",)), request, params
        )
        if widget is None:
            return data
        payload: dict[str, typing.Any] = {"days": data.get("days"), "widget": widget, widget: data.get(widget)}
        return payload

    return ToolDefinition(
        name="get_dashboard",
        title="Get diagnostics dashboard",
        description=(
            "Aggregated platform diagnostics, the same data the administration dashboard "
            "shows over a look-back window (``days``, default 30, up to 365): point-in-time "
            "kpis, peak concurrency, pool saturation, cache efficiency, tunnel usage, "
            "client platforms, top users, session duration, userservice errors and failed "
            "logins. Pass a single ``widget`` (one of "
            f"{', '.join(_DASHBOARD_WIDGETS)}) to get only that slice; omit it for the "
            "full payload. Use it to triage platform health before drilling into a "
            "specific pool or user. Administrators only."
        ),
        input_schema=schema(
            {
                "days": {
                    "type": "integer",
                    "description": "Look-back window in days (1..365). Default: 30.",
                },
                "widget": string_property(
                    "Optional single widget to return. Omit for the whole dashboard payload."
                ),
            }
        ),
        access="Administrators only (the backing dashboard endpoint requires the admin role).",
        returns=(
            "The dashboard payload: kpis plus one entry per widget, each bounded to its "
            "top rows; or a single widget when one was requested."
        ),
        required_permission="ALL",
        executor=executor,
    )


def curated_tools() -> tuple[ToolDefinition, ...]:
    """Return the platform tools."""
    return (
        _platform_stats_tool(),
        _security_check_tool(),
        _dashboard_tool(),
        _config_tool(),
    )
