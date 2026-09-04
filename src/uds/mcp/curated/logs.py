"""Log tools: the unified per-object trail and the admin-only global log."""

import typing

from asgiref.sync import sync_to_async

from uds.core import consts
from uds.REST.handlers import Handler
from uds.REST.methods.authenticators import Authenticators, Users
from uds.REST.methods.logs import Logs
from uds.REST.methods.meta_pools import MetaPools
from uds.REST.methods.meta_service_pools import MetaServicesPool
from uds.REST.methods.providers import Providers
from uds.REST.methods.services import Services
from uds.REST.methods.services_pools import ServicesPools
from uds.REST.methods.user_services import AssignedUserService

from ..catalog import ToolDefinition
from ..rest_proxy import RestProxy, RestTarget
from .helpers import GET, JsonObject, check_required, schema, string_property, uuid_property

__all__ = ["curated_tools"]


def _item_logs_tool() -> ToolDefinition:
    """Build the unified per-object logs tool.

    The ``log`` modifier is hardcoded in ``ModelHandler.get`` and
    ``DetailHandler.get`` for every collection implementing ``get_logs``.
    One tool with a ``collection`` discriminator beats five near-identical
    tools; the mapping below is the single place that knows which handler
    serves each collection.
    """

    async def executor(arguments: JsonObject, request: typing.Any = None) -> typing.Any:
        check_required(arguments, ("collection", "uuid"))
        collection = str(arguments["collection"])
        item_id = arguments.get("item_id")

        if collection == "service_pool":
            target = RestTarget(ServicesPools, "servicespools", GET, args=(str(arguments["uuid"]), consts.rest.LOG))
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
            GET,
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
        input_schema=schema(
            {
                "collection": string_property(
                    "What the logs belong to: service_pool, user, service, meta_pool_member or assigned_service."
                ),
                "uuid": uuid_property(
                    "UUID of the object: the pool for service_pool, or the parent "
                    "(authenticator, provider, meta pool, service pool) for the rest."
                ),
                "item_id": string_property(
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

    async def executor(arguments: JsonObject, request: typing.Any = None) -> typing.Any:
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
        input_schema=schema(
            {
                "since": string_property(
                    "Only entries at or after this instant. ISO 8601 datetime "
                    "(2026-09-04T10:00:00) or plain date (2026-09-04, start of that day)."
                ),
                "until": string_property(
                    "Only entries at or before this instant. Same formats; a plain "
                    "date means the end of that day (23:59:59)."
                ),
                "limit": {
                    "type": "integer",
                    "description": "Page size (default 100, capped at 1000). Ignored when using top.",
                    "minimum": 1,
                    "maximum": 1000,
                },
                "level": string_property("Minimum severity as a level name (INFO, WARNING, ERROR...)."),
                "source": string_property("Exact, case-insensitive source match (REST, SERVICE, INTERNAL...)."),
                "filter": string_property(
                    'Raw OData $filter over the log fields, e.g. "contains(data,\'timeout\')" or "level ge 40000".'
                ),
                "orderby": string_property(
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


def curated_tools() -> tuple[ToolDefinition, ...]:
    """Return the log tools."""
    return (
        _item_logs_tool(),
        _system_logs_tool(),
    )
