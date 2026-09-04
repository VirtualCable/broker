"""Hand-curated, purpose-specific MCP tools.

The generic ``list_*`` generator only covers model collections. These tools
expose the high-value read-only surfaces that need a crafted schema and
description:

* the GET custom methods selected by hand (fallback access policies,
  service pool forecasting, cache recommendations, server statistics,
  authenticator user/group search);
* the per-object log modifier (``<uuid>/log``) that ``ModelHandler`` and
  ``DetailHandler`` expose for collections that implement ``get_logs``,
  unified here as a single ``get_item_logs`` tool;
* the admin-only global log endpoint (``uds.REST.methods.logs``);
* the analytical surfaces that the listings cannot provide: platform-wide
  usage counters, the security self-assessment and the CSV reports that
  aggregate data (failed logins, admin activity).

Every executor forwards the live request through :class:`RestProxy`, so the
REST permission checks of each target handler stay in force: a staff
identity without read permission on the target item gets an access-denied
error, and the admin-only surfaces (global log, security check, reports,
platform-wide stats) only work for administrators.
"""

import typing

from asgiref.sync import sync_to_async

from uds.core import consts, types
from uds.REST.handlers import Handler
from uds.REST.methods.authenticators import Authenticators, Users
from uds.REST.methods.logs import Logs
from uds.REST.methods.meta_pools import MetaPools
from uds.REST.methods.meta_service_pools import MetaServicesPool
from uds.REST.methods.providers import Providers
from uds.REST.methods.reports import Reports
from uds.REST.methods.services import Services
from uds.REST.methods.services_pools import ServicesPools
from uds.REST.methods.servers_management import ServersGroups, ServersServers
from uds.REST.methods.system import System
from uds.REST.methods.user_services import AssignedUserService
from uds.reports.lists.admin_activity import AdminActivityReportCSV
from uds.reports.lists.failed_logins import FailedLoginsReportCSV

from .catalog import Catalog, ToolDefinition
from .rest_proxy import RestProxy, RestTarget

if typing.TYPE_CHECKING:
    from uds.core.reports.report import Report

_GET = types.rest.CustomMethodMethod.GET
_POST = types.rest.CustomMethodMethod.POST

_JsonObject = dict[str, typing.Any]

# Upper bound for the CSV text a report tool may return. Reports are
# aggregations, but an unhappy configuration (or a very active broker) can
# still produce big documents; LLM clients pay per token, so the answer is
# clamped here with an explicit truncation marker.
_MAX_REPORT_CHARS: typing.Final[int] = 65536


def _schema(properties: _JsonObject, required: tuple[str, ...] = ()) -> _JsonObject:
    """Build an object input schema with the catalog's conventions."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _string_property(description: str) -> _JsonObject:
    return {"type": "string", "description": description}


def _uuid_property(description: str) -> _JsonObject:
    return {"type": "string", "description": description}


def _check_required(arguments: _JsonObject, names: tuple[str, ...]) -> None:
    """Raise ``ValueError`` (surfaced as MCP ``invalid params``) on missing args.

    The JSON-Schema subset the server validates does not include
    ``required``, so the executor enforces it before touching the proxy.
    Empty or whitespace-only strings count as missing; other types only
    need to be present.
    """
    for name in names:
        value = arguments.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"{name} is required")


def _master_custom_tool(
    *,
    name: str,
    title: str,
    description: str,
    handler: type[Handler],
    path: str,
    custom_name: str,
    uuid_property: _JsonObject,
    extra_properties: _JsonObject | None = None,
    extra_required: tuple[str, ...] = (),
    access: str,
    returns: str,
) -> ToolDefinition:
    """Build a tool around a ``needs_parent`` GET custom method of a master handler.

    The URL arguments are known only at call time (they carry the target
    item's uuid), so the executor assembles the :class:`RestTarget` on every
    invocation. Everything in the arguments besides the uuid travels to the
    handler as query parameters, exactly like a direct REST call.
    """

    async def executor(arguments: _JsonObject, request: typing.Any = None) -> typing.Any:
        _check_required(arguments, ("uuid", *extra_required))
        params = {key: value for key, value in arguments.items() if key != "uuid"}
        target = RestTarget(handler, path, _GET, args=(str(arguments["uuid"]), custom_name))
        return await RestProxy().execute(target, request, params)

    properties: _JsonObject = {"uuid": uuid_property}
    if extra_properties:
        properties.update(extra_properties)

    return ToolDefinition(
        name=name,
        title=title,
        description=description,
        input_schema=_schema(properties, ("uuid", *extra_required)),
        access=access,
        returns=returns,
        required_permission="READ",
        executor=executor,
    )


def _server_stats_tool() -> ToolDefinition:
    """Build the per-server resource usage tool (a detail custom method).

    The target is a ``DetailHandler`` custom method under
    ``servers/groups``, so the executor resolves the parent group (its uuid
    carries the permission check) and then dispatches the detail handler
    with ``(server_uuid, "stats")`` as URL arguments.
    """

    target_parent = RestTarget(ServersGroups, "servers/groups")

    async def executor(arguments: _JsonObject, request: typing.Any = None) -> typing.Any:
        _check_required(arguments, ("group_uuid", "server_uuid"))
        params = {key: value for key, value in arguments.items() if key not in ("group_uuid", "server_uuid")}
        target = RestTarget(
            ServersServers,
            "servers/groups/{uuid}/servers",
            _GET,
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
        input_schema=_schema(
            {
                "group_uuid": _uuid_property("UUID of the server group the server belongs to."),
                "server_uuid": _uuid_property("UUID of the server (list them with ``list_servers_servers``)."),
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


def _item_logs_tool() -> ToolDefinition:
    """Build the unified per-object logs tool.

    The ``log`` modifier is hardcoded in ``ModelHandler.get`` and
    ``DetailHandler.get`` for every collection implementing ``get_logs``.
    One tool with a ``collection`` discriminator beats five near-identical
    tools; the mapping below is the single place that knows which handler
    serves each collection.
    """

    async def executor(arguments: _JsonObject, request: typing.Any = None) -> typing.Any:
        _check_required(arguments, ("collection", "uuid"))
        collection = str(arguments["collection"])
        item_id = arguments.get("item_id")

        if collection == "service_pool":
            target = RestTarget(ServicesPools, "servicespools", _GET, args=(str(arguments["uuid"]), consts.rest.LOG))
            return await RestProxy().execute(target, request, {})

        details: dict[str, tuple[type[Handler], str, type[Handler], str]] = {
            "user": (Users, "authenticators/{uuid}/users", Authenticators, "authenticators"),
            "service": (Services, "providers/{uuid}/services", Providers, "providers"),
            "meta_pool_member": (MetaServicesPool, "metapools/{uuid}/services", MetaPools, "metapools"),
            "assigned_service": (
                AssignedUserService,
                "servicespools/{uuid}/services",
                ServicesPools,
                "servicespools",
            ),
        }
        if collection not in details:
            raise ValueError(
                f"Unknown collection: {collection} (expected one of service_pool, user, service, meta_pool_member, assigned_service)"
            )
        if not isinstance(item_id, str) or not item_id:
            raise ValueError(f"item_id is required for collection {collection}")

        detail_cls, detail_path, parent_cls, parent_path = details[collection]
        target = RestTarget(
            detail_cls,
            detail_path,
            _GET,
            args=(item_id, consts.rest.LOG),
            parent=RestTarget(parent_cls, parent_path),
        )
        return await sync_to_async(RestProxy._execute_sync, thread_sensitive=True)(
            target, request, {}, str(arguments["uuid"])
        )

    return ToolDefinition(
        name="get_item_logs",
        title="Get item logs",
        description=(
            "Read the UDS log trail of one object. Use ``collection`` to select what "
            "the ``uuid`` refers to: ``service_pool`` (the pool itself), ``user`` (a user "
            "of an authenticator, requires ``item_id``), ``service`` (a service of a "
            "provider, requires ``item_id``), ``meta_pool_member`` (a pool member of a "
            "meta pool, requires ``item_id``) or ``assigned_service`` (a deployed service "
            "of a pool, requires ``item_id``)."
        ),
        input_schema=_schema(
            {
                "collection": _string_property(
                    "What the logs belong to: service_pool, user, service, meta_pool_member or assigned_service."
                ),
                "uuid": _uuid_property(
                    "UUID of the object: the pool for service_pool, or the parent "
                    "(authenticator, provider, meta pool, service pool) for the rest."
                ),
                "item_id": _string_property(
                    "Id or uuid of the detail item; required for every collection except service_pool."
                ),
            },
            ("collection", "uuid"),
        ),
        access="Available to authenticated UDS users with read permission on the target object.",
        returns="An array of log entries (date, level, source, message), oldest first.",
        required_permission="READ",
        executor=executor,
    )


def _system_logs_tool() -> ToolDefinition:
    """Build the global (system) log tool on top of the admin REST endpoint."""

    async def executor(arguments: _JsonObject, request: typing.Any = None) -> typing.Any:
        params: dict[str, typing.Any] = {}
        if arguments.get("filter") is not None:
            params["$filter"] = arguments["filter"]
        if arguments.get("orderby") is not None:
            params["$orderby"] = arguments["orderby"]
        if arguments.get("skip") is not None:
            params["$skip"] = arguments["skip"]
        for key in ("level", "limit", "source", "since", "until"):
            if arguments.get(key) is not None:
                params[key] = arguments[key]
        # The default RestTarget method (QUERY, RFC 10008) makes the
        # handler pull the ``$``-prefixed keys from the parameters, which
        # is how the OData machinery receives them outside a querystring.
        return await RestProxy().execute(RestTarget(Logs, "logs"), request, params)

    return ToolDefinition(
        name="get_system_logs",
        title="Get system logs",
        description=(
            "Read the global (system) UDS log: publications, actor registrations, "
            "REST/MCP operations and platform decisions, newest first. "
            "Filtering is twofold: friendly arguments (since/until/level/source) "
            "and raw OData (filter/orderby/skip) over the log fields for anything else. "
            "The answer is an object with ``entries``, ``truncated`` and ``limit``: "
            "when ``truncated`` is true, more entries matched the filters than the "
            "page allowed — follow the included ``hint`` to get the rest. "
            "**Administrators only**; non-admin identities get an access-denied error."
        ),
        input_schema=_schema(
            {
                "since": _string_property(
                    "Only entries at or after this instant. ISO 8601 datetime "
                    "(2026-09-04T10:00:00) or plain date (2026-09-04, start of that day)."
                ),
                "until": _string_property(
                    "Only entries at or before this instant. Same formats; a plain "
                    "date means the end of that day (23:59:59)."
                ),
                "limit": {
                    "type": "integer",
                    "description": "Page size (default 100, capped at 1000). Ignored when using top.",
                    "minimum": 1,
                    "maximum": 1000,
                },
                "level": _string_property("Minimum severity as a level name (INFO, WARNING, ERROR...)."),
                "source": _string_property("Exact, case-insensitive source match (REST, SERVICE, INTERNAL...)."),
                "filter": _string_property(
                    'Raw OData $filter over the log fields, e.g. "contains(data,\'timeout\')" or "level ge 40000".'
                ),
                "orderby": _string_property(
                    "OData $orderby over log fields, e.g. 'created asc'. Default: newest first."
                ),
                "skip": {"type": "integer", "description": "Entries to skip before the page (paging).", "minimum": 0},
            }
        ),
        access="Administrators only (the backing REST endpoint requires the admin role).",
        returns="An object with entries (date, level, level_name, source, message), truncated and limit.",
        required_permission="ALL",
        executor=executor,
    )


def _platform_stats_tool() -> ToolDefinition:
    """Build the platform-wide usage counters tool (``/system/stats``)."""

    _COUNTERS: typing.Final[tuple[str, ...]] = ("assigned", "inuse", "cached", "complete")

    async def executor(arguments: _JsonObject, request: typing.Any = None) -> typing.Any:
        counter = str(arguments.get("counter", "")).lower()
        if counter not in _COUNTERS:
            raise ValueError(f"counter must be one of {', '.join(_COUNTERS)}")
        args = ["stats", counter]
        pool_uuid = arguments.get("pool_uuid")
        if pool_uuid:
            args.append(str(pool_uuid))
        return await RestProxy().execute(RestTarget(System, "system", _GET, args=tuple(args)), request, {})

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
        input_schema=_schema(
            {
                "counter": _string_property("Which series: assigned, inuse, cached or complete."),
                "pool_uuid": _uuid_property(
                    "Optional UUID of a service pool to scope the series to. "
                    "Omit for platform-wide (administrators only)."
                ),
            },
            ("counter",),
        ),
        access=("Platform-wide series: administrators only. Per-pool series: staff with read permission on the pool."),
        returns="An array of counter samples (or an object with the three series for complete).",
        required_permission="READ",
        executor=executor,
    )


def _security_check_tool() -> ToolDefinition:
    """Build the security self-assessment tool (``/system/security_check``)."""

    async def executor(arguments: _JsonObject, request: typing.Any = None) -> typing.Any:
        return await RestProxy().execute(RestTarget(System, "system", _GET, args=("security_check",)), request, {})

    return ToolDefinition(
        name="get_security_check",
        title="Get security check",
        description=(
            "Security self-assessment of this broker: configuration findings that an "
            "administrator should review (weak settings, insecure defaults, ...). "
            "Administrators only; staff get an access-denied error."
        ),
        input_schema=_schema({}),
        access="Administrators only.",
        returns="An object with the security findings and their severity.",
        required_permission="ALL",
        executor=executor,
    )


def _report_tool(
    *,
    name: str,
    title: str,
    description: str,
    report_cls: "type[Report]",
    params_schema: _JsonObject,
    required: tuple[str, ...],
    argument_to_param: dict[str, str],
    defaults: dict[str, typing.Any],
    access: str,
) -> ToolDefinition:
    """Build a tool that generates one specific CSV report.

    The report uuid is fixed by the tool (the class is the single source of
    truth through ``get_uuid``), so the agent never has to discover ids; the
    arguments are exactly the report's own parameters. The generated CSV
    text is clamped to ``_MAX_REPORT_CHARS`` with an explicit truncation
    marker, so answers stay proportionate for an LLM context.
    """
    report_uuid = report_cls.get_uuid()

    async def executor(arguments: _JsonObject, request: typing.Any = None) -> typing.Any:
        _check_required(arguments, required)
        params: dict[str, typing.Any] = dict(defaults)
        for argument, param in argument_to_param.items():
            if arguments.get(argument) is not None:
                params[param] = arguments[argument]
        result = await RestProxy().execute(
            RestTarget(Reports, "reports", _POST, args=(report_uuid,)),
            request,
            params,
        )
        data = str(typing.cast(_JsonObject, result).get("data", ""))
        truncated = len(data) > _MAX_REPORT_CHARS
        return {
            "mime_type": result.get("mime_type"),
            "filename": result.get("filename"),
            "data": data[:_MAX_REPORT_CHARS],
            "truncated": truncated,
            **(
                {
                    "hint": (
                        f"The report is bigger than {_MAX_REPORT_CHARS} characters and was cut. "
                        "Narrow the date range or the scope to get the rest."
                    )
                }
                if truncated
                else {}
            ),
        }

    return ToolDefinition(
        name=name,
        title=title,
        description=description,
        input_schema=_schema(params_schema, required),
        access=access,
        returns="An object with mime_type, filename, the CSV text in data, and truncated when the text was cut.",
        required_permission="ALL",
        executor=executor,
    )


def _failed_logins_tool() -> ToolDefinition:
    """Build the failed logins CSV report tool."""

    return _report_tool(
        name="report_failed_logins",
        title="Report: failed logins",
        description=(
            "CSV aggregation of failed login attempts over a date range, per user and "
            "authenticator. Use it to assess brute-force or account-lockout situations; "
            "raw lines live in ``get_system_logs``, this answer is the counted summary."
        ),
        report_cls=FailedLoginsReportCSV,
        params_schema={
            "start_date": _string_property("First day of the range, as YYYY-MM-DD."),
            "end_date": _string_property("Last day of the range, as YYYY-MM-DD (inclusive)."),
            "authenticator_uuid": _uuid_property(
                "Optional authenticator to scope the report to. Omit (or pass the special "
                "value 0-0-0-0) for all authenticators."
            ),
        },
        required=("start_date", "end_date"),
        argument_to_param={"start_date": "start_date", "end_date": "end_date", "authenticator_uuid": "authenticator"},
        defaults={"authenticator": "0-0-0-0"},
        access="Administrators only (the backing reports endpoint requires the admin role).",
    )


def _admin_activity_tool() -> ToolDefinition:
    """Build the admin activity CSV report tool."""

    return _report_tool(
        name="report_admin_activity",
        title="Report: admin activity",
        description=(
            "CSV summary of administrator activity over a date range: requests, errors, "
            "last seen and most used endpoints per admin. Use it to review what "
            "administrators (and automated agents) actually did."
        ),
        report_cls=AdminActivityReportCSV,
        params_schema={
            "start_date": _string_property("First day of the range, as YYYY-MM-DD."),
            "end_date": _string_property("Last day of the range, as YYYY-MM-DD (inclusive)."),
            "top_paths": {
                "type": "integer",
                "description": "Most-used endpoints to list per admin (1-50). Default: 5.",
                "minimum": 1,
                "maximum": 50,
            },
        },
        required=("start_date", "end_date", "top_paths"),
        argument_to_param={"start_date": "start_date", "end_date": "end_date", "top_paths": "top_paths"},
        defaults={},
        access="Administrators only (the backing reports endpoint requires the admin role).",
    )


def curated_tools() -> tuple[ToolDefinition, ...]:
    """Return the hand-curated tool set for the default catalog."""
    return (
        _master_custom_tool(
            name="get_servicepool_fallback_access",
            title="Get service pool fallback access",
            description="Current fallback access policy of a service pool (what happens when no calendar rule applies).",
            handler=ServicesPools,
            path="servicespools",
            custom_name="fallback_access",
            uuid_property=_uuid_property("UUID of the service pool."),
            access="Available to authenticated UDS users with read permission on the pool.",
            returns="The pool's fallback access policy.",
        ),
        _master_custom_tool(
            name="get_metapool_fallback_access",
            title="Get meta pool fallback access",
            description="Current fallback access policy of a meta pool (what happens when no calendar rule applies).",
            handler=MetaPools,
            path="metapools",
            custom_name="fallback_access",
            uuid_property=_uuid_property("UUID of the meta pool."),
            access="Available to authenticated UDS users with read permission on the meta pool.",
            returns="The meta pool's fallback access policy.",
        ),
        _master_custom_tool(
            name="get_servicepool_forecast",
            title="Get service pool usage forecast",
            description="Forecast future usage of a service pool from its historical weekly profile.",
            handler=ServicesPools,
            path="servicespools",
            custom_name="forecast",
            uuid_property=_uuid_property("UUID of the service pool."),
            extra_properties={
                "counter": _string_property("Counter to forecast (inuse, assigned or cached). Default: inuse."),
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
        _master_custom_tool(
            name="get_servicepool_cache_recommendations",
            title="Get service pool cache recommendations",
            description="Cache sizing recommendations for a service pool based on predicted usage patterns.",
            handler=ServicesPools,
            path="servicespools",
            custom_name="cache_recommendations",
            uuid_property=_uuid_property("UUID of the service pool."),
            access="Available to authenticated UDS users with read permission on the pool.",
            returns="Recommended cache configuration with the current one and per-level verdicts.",
        ),
        _master_custom_tool(
            name="get_server_group_stats",
            title="Get server group stats",
            description="Aggregate statistics of a server group: per-server status, load and weights.",
            handler=ServersGroups,
            path="servers/groups",
            custom_name="stats",
            uuid_property=_uuid_property("UUID of the server group."),
            access="Available to authenticated UDS users with read permission on the group.",
            returns="Per-server statistics for the group.",
        ),
        _master_custom_tool(
            name="search_authenticator",
            title="Search authenticator users or groups",
            description="Search users or groups of an authenticator by name or identifier.",
            handler=Authenticators,
            path="authenticators",
            custom_name="search",
            uuid_property=_uuid_property("UUID of the authenticator to search in."),
            extra_properties={
                "type": _string_property("What to search for: user or group."),
                "term": _string_property("Search text to match against user or group names."),
                "limit": {"type": "integer", "description": "Maximum number of results. Default: 50.", "minimum": 1},
            },
            extra_required=("type", "term"),
            access="Available to authenticated UDS users with read permission on the authenticator.",
            returns="An array of matching users or groups as reported by the authenticator.",
        ),
        _server_stats_tool(),
        _item_logs_tool(),
        _system_logs_tool(),
        _platform_stats_tool(),
        _security_check_tool(),
        _failed_logins_tool(),
        _admin_activity_tool(),
    )


def register_curated_tools(catalog: Catalog) -> None:
    """Register every curated tool on the given catalog."""
    for tool in curated_tools():
        catalog.add_tool(tool)
