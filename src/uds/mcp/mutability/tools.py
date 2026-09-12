"""MCP tools for the supervised mutability.

One proposal tool per registered ``MutableActionType`` (generated from
the registry), one discovery tool and the three management tools the
agent uses to iterate on its own proposals. Nothing here applies a
change: proposals wait until an administrator approves them from the
administration interface (phase 3 of doc/plan/mcp-mutability.md).

The proposal lifecycle tools do NOT touch the models directly: they
invoke the ``/flows/own`` REST surface (the same one any API client
uses), so ownership, caps, validation and CAS behave identically for
MCP and REST consumers. Like the REST proxy, every executor runs its
(synchronous) work inside a ``sync_to_async`` boundary, because Django
ORM access is not allowed from the MCP async event loop.
"""

import collections.abc
import datetime
import typing

from asgiref.sync import sync_to_async

from uds.core.consts import mcp as consts_mcp
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.mcp import FlowStatus
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.core.util.model import sql_now
from uds.models import User

from uds.mcp.catalog import Catalog, ToolDefinition
from uds.mutability import registry
from uds.mutability.base import JsonObject, REDACTED

JsonDict = dict[str, typing.Any]

ExecutorSync = collections.abc.Callable[[JsonObject, ExtendedHttpRequestWithUser], typing.Any]

_PROPOSE_MESSAGE: typing.Final[str] = (
    "Proposal queued. It does NOT take effect until an administrator approves it."
)
_UPDATE_MESSAGE: typing.Final[str] = (
    "Proposal updated. It does NOT take effect until an administrator approves it."
)


def _request_user(request: ExtendedHttpRequestWithUser) -> User:
    """The UDS user behind the MCP request."""
    user: User | None = getattr(request, "user", None)
    if user is None or not user.uuid:
        raise rest_exceptions.AccessDenied()
    return user


def _flows_rest(
    request: ExtendedHttpRequestWithUser, method: str, params: JsonObject, *args: str
) -> typing.Any:
    """Invoke one ``/flows/own`` operation with the full handler lifecycle.

    The handler resolves authentication and permissions from the real
    request, exactly as the REST dispatcher would; detail routing goes
    through ``process_detail`` and its checks.
    """
    from uds.REST.methods.flows import FlowsOwn

    handler = FlowsOwn(request, "flows/own", method, params, *args)
    return getattr(handler, method)()


def _describe_item(action_type: registry.MutableActionType, item: JsonDict) -> JsonObject:
    """Masked, registry-shaped description of one action item.

    Mirrors ``MutableActionType.describe`` but built from the REST item
    (the proxy never handles models). If the target has vanished, every
    value is masked: a broken lookup must not become a leak.
    """
    changes: JsonDict = dict(typing.cast("JsonDict", item.get("values") or {}))
    try:
        target = action_type.resolve_target(str(item["target_uuid"]))
        secrets = action_type.secret_names(action_type.for_type_of(target), target)
    except Exception:
        secrets = set(changes)
    return {
        "type": item["action_type"],
        "target": item["target_uuid"],
        "status": item["status"],
        "changes": {name: (REDACTED if name in secrets else changes.get(name)) for name in sorted(changes)},
        "secrets_changed": sorted(set(changes) & secrets),
    }


def _as_dict(obj: typing.Any) -> typing.Any:
    """In-process responses carry dataclasses (no JSON serialization)."""
    return obj.as_dict() if hasattr(obj, "as_dict") else obj


def _detail_result(response: typing.Any) -> JsonDict:
    """Unwrap the ``rest_result`` envelope of detail write operations."""
    assert isinstance(response, dict) and "result" in response
    return typing.cast("JsonDict", _as_dict(response["result"]))


def _registry_type(type_id: str) -> registry.MutableActionType:
    found = registry.get(type_id)
    if found is None:
        raise ValueError(f"Unknown action type {type_id}")
    return found()


def _own_action_index(
    request: ExtendedHttpRequestWithUser, statuses: collections.abc.Container[str]
) -> dict[str, tuple[str, JsonDict]]:
    """Index of the caller's actions: id -> (flow_id, item), flow status filtered.

    The index is built from the REST surface, so ownership is already
    enforced there; ``statuses`` selects which flow states are eligible
    (pending to mutate, expired to revive).
    """
    index: dict[str, tuple[str, JsonDict]] = {}
    for flow_raw in typing.cast("list[typing.Any]", _flows_rest(request, "get", {})):
        flow = typing.cast("JsonDict", _as_dict(flow_raw))
        if flow["status"] not in statuses:
            continue
        items = typing.cast("list[typing.Any]", _flows_rest(request, "get", {}, str(flow["id"]), "actions"))
        for item_raw in items:
            item = typing.cast("JsonDict", _as_dict(item_raw))
            index[str(item["id"])] = (str(flow["id"]), item)
    return index


def _parse_date_arg(value: typing.Any, name: str) -> datetime.datetime | None:
    """Parse an optional ISO date/datetime argument (UTC for naive values)."""
    if value in (None, ""):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"{name} must be an ISO date or datetime") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _wrap_sync(sync_body: ExecutorSync) -> collections.abc.Callable[..., typing.Any]:
    """Executor wrapper: run the sync body off the event loop.

    The HTTP request is mandatory here: the whole surface resolves
    ownership and permissions through it.
    """

    async def executor(arguments: JsonObject, request: ExtendedHttpRequestWithUser | None = None) -> typing.Any:
        if request is None:
            raise rest_exceptions.AccessDenied()
        return await sync_to_async(sync_body, thread_sensitive=True)(arguments, request)

    return executor


def _propose_sync(
    action_type: registry.MutableActionType,
    arguments: JsonObject,
    request: ExtendedHttpRequestWithUser,
) -> JsonDict:
    _request_user(request)
    target_uuid = str(arguments.get("target_uuid", "") or "")
    if not target_uuid.strip():
        raise ValueError("target_uuid is required")
    values = arguments.get("values")
    if not isinstance(values, dict) or not values:
        raise ValueError("values is required and must be a non-empty object")
    values = typing.cast("dict[str, typing.Any]", values)
    justification = str(arguments.get("justification", "") or "")

    # The flow is created first, then the action is appended through the
    # detail surface (validation, MANAGEMENT over the target, CAS base
    # and caps are the REST ones). If the action fails, the fresh flow
    # is withdrawn: proposals never end up half-created.
    flow_params: JsonObject = {
        "name": action_type.title,
        "justification": justification,
        "expires_in_hours": arguments.get("expires_in_hours", ""),
    }
    flow = typing.cast("JsonDict", _flows_rest(request, "post", flow_params))
    try:
        action = _detail_result(
            _flows_rest(
                request,
                "post",
                {
                    "action_type": action_type.type_id,
                    "target_uuid": target_uuid,
                    "values": values,
                    "justification": justification,
                },
                str(flow["id"]),
                "actions",
            )
        )
    except Exception:
        try:
            _flows_rest(request, "delete", {}, str(flow["id"]))
        except Exception:
            pass
        raise
    return {
        "id": action["id"],
        "flow_id": flow["id"],
        "status": action["status"],
        "message": _PROPOSE_MESSAGE,
        "proposal": _describe_item(action_type, action),
    }


def _discovery_sync(arguments: JsonObject, request: ExtendedHttpRequestWithUser) -> JsonDict:
    _request_user(request)
    type_id = str(arguments.get("action_type", "") or "provider.update")
    found = registry.get(type_id)
    if found is None:
        known = ", ".join(t.type_id for t in registry.all_types())
        raise ValueError(f"Unknown action type {type_id} (known: {known})")
    action_type = found()

    target_uuid = str(arguments.get("target_uuid", "") or "")
    for_type = str(arguments.get("for_type", "") or "")
    result: JsonDict = {"action_type": type_id}
    if target_uuid.strip():
        target = action_type.resolve_target(target_uuid)
        for_type = action_type.for_type_of(target)
        result["target_uuid"] = target_uuid
        result["for_type"] = for_type
        fields = action_type.field_definitions(for_type, target)
        result["fields"] = fields
        result["current_values"] = action_type.snapshot_values(target, [d["name"] for d in fields])
    elif for_type.strip():
        if action_type.target_scoped_fields:
            # Detail types build their gui from the parent item, so a bare
            # subtype is not enough to enumerate their fields.
            raise ValueError(f"{type_id} requires target_uuid: its fields depend on the concrete target")
        result["for_type"] = for_type
        result["fields"] = action_type.field_definitions(for_type)
    else:
        raise ValueError("Provide either target_uuid or for_type")
    return result


def _list_sync(arguments: JsonObject, request: ExtendedHttpRequestWithUser) -> JsonDict:
    _request_user(request)
    status_raw = str(arguments.get("status", "") or "")
    type_filter = arguments.get("action_type")
    # Visibility horizon: the agent sees its own recent history only.
    # Explicit date bounds win over the default; the database (and the
    # administrator surfaces) keep everything.
    horizon = sql_now() - datetime.timedelta(days=consts_mcp.VISIBILITY_DAYS)
    created_after = _parse_date_arg(arguments.get("created_after"), "created_after") or horizon
    created_before = _parse_date_arg(arguments.get("created_before"), "created_before")
    result: list[JsonDict] = []
    for flow_raw in typing.cast("list[typing.Any]", _flows_rest(request, "get", {})):
        flow = typing.cast("JsonDict", _as_dict(flow_raw))
        if flow["created"] < created_after:
            continue
        if created_before is not None and flow["created"] > created_before:
            continue
        for action_raw in typing.cast(
            "list[typing.Any]", _flows_rest(request, "get", {}, str(flow["id"]), "actions")
        ):
            action = typing.cast("JsonDict", _as_dict(action_raw))
            if type_filter and action["action_type"] != str(type_filter):
                continue
            # Status filter matches either the action lifecycle or the
            # decision taken on its flow (cancelled/rejected/expired are
            # flow-level states).
            if status_raw and action["status"] != status_raw and flow["status"] != status_raw:
                continue
            item: JsonDict = {
                "id": action["id"],
                "flow": {
                    "id": flow["id"],
                    "name": flow["name"],
                    "status": flow["status"],
                },
                "type": action["action_type"],
                "target": action["target_uuid"],
                "status": action["status"],
                "created": action["created"].isoformat(),
                "result": action.get("result"),
            }
            if flow["status"] == FlowStatus.PENDING:
                try:
                    item["proposal"] = _describe_item(_registry_type(str(action["action_type"])), action)
                except Exception:
                    values = typing.cast("JsonDict", action.get("values") or {})
                    item["proposal"] = {"changes": {name: REDACTED for name in sorted(values)}}
            else:
                item["decided_by"] = flow.get("decided_by")
            result.append(item)
    return {"items": result, "count": len(result)}


def _update_sync(arguments: JsonObject, request: ExtendedHttpRequestWithUser) -> JsonDict:
    _request_user(request)
    action_id = str(arguments.get("id", "") or "")
    if not action_id.strip():
        raise ValueError("id is required")
    values = arguments.get("values")
    if not isinstance(values, dict) or not values:
        raise ValueError("values is required and must be a non-empty object")
    values = typing.cast("dict[str, typing.Any]", values)

    # Pending flows are edited; expired ones come back to life through
    # the same PUT (the REST surface reopens them within the grace
    # window). The index is built from the REST surface, so ownership
    # is already enforced there.
    found = _own_action_index(request, (FlowStatus.PENDING, FlowStatus.EXPIRED)).get(action_id)
    if found is None:
        raise ValueError(f"No pending action with id {action_id}")
    flow_id, item = found

    # Full replacement of the proposal, re-based on the current state
    # (the REST detail PUT validates and snapshots the new base).
    action = _detail_result(
        _flows_rest(
            request,
            "put",
            {"values": values, "expires_in_hours": arguments.get("expires_in_hours", "")},
            flow_id,
            "actions",
            action_id,
        )
    )
    return {
        "id": action_id,
        "flow_id": flow_id,
        "status": action["status"],
        "message": _UPDATE_MESSAGE,
        "proposal": _describe_item(_registry_type(str(item["action_type"])), action),
    }


def _cancel_sync(arguments: JsonObject, request: ExtendedHttpRequestWithUser) -> JsonDict:
    _request_user(request)
    action_id = str(arguments.get("id", "") or "")
    if not action_id.strip():
        raise ValueError("id is required")

    found = _own_action_index(request, (FlowStatus.PENDING,)).get(action_id)
    if found is None:
        raise ValueError(f"No pending action with id {action_id}")
    flow_id, item = found

    # Decisions are flow-level: cancelling withdraws the whole proposal
    flow = typing.cast("JsonDict", _flows_rest(request, "delete", {}, flow_id))
    # Pending actions get skipped by the decision; refresh from the surface
    status = item["status"]
    try:
        actions = typing.cast("list[typing.Any]", _flows_rest(request, "get", {}, flow_id, "actions"))
        normalized = (typing.cast("JsonDict", _as_dict(a)) for a in actions)
        refreshed = next((ref for ref in normalized if str(ref["id"]) == action_id), None)
        if refreshed is not None:
            status = refreshed["status"]
    except Exception:
        pass
    return {
        "id": action_id,
        "flow_id": flow_id,
        "status": status,
        "flow_status": flow["status"],
        "message": "Proposal cancelled.",
    }


def _propose_tool(action_type: registry.MutableActionType) -> ToolDefinition:
    """Build the proposal tool of one registered action type."""

    def sync_body(arguments: JsonObject, request: ExtendedHttpRequestWithUser) -> JsonDict:
        return _propose_sync(action_type, arguments, request)

    return ToolDefinition(
        name=f"propose_{action_type.type_id.replace('.', '_')}",
        title=action_type.title,
        description=action_type.description,
        input_schema={
            "type": "object",
            "properties": {
                "target_uuid": {
                    "type": "string",
                    "description": "UUID of the item to mutate.",
                },
                "values": {
                    "type": "object",
                    "description": (
                        "Field values to propose, in the same shape the REST API accepts. "
                        "Use get_mutable_fields to discover the accepted fields, their types "
                        "and current values."
                    ),
                    "additionalProperties": True,
                },
                "justification": {
                    "type": "string",
                    "description": (
                        "Why this change is needed. The administrator reads it to decide, "
                        "so be specific: reference the ticket, request or reason."
                    ),
                },
                "expires_in_hours": {
                    "type": "integer",
                    "description": (
                        "Hours you expect an administrator to need to resolve this proposal "
                        "(1..720, default 7 days). The proposal auto-expires after it, so "
                        "declare a realistic window."
                    ),
                },
            },
            "required": ["target_uuid", "values"],
            "additionalProperties": False,
        },
        access="Staff with MANAGEMENT permission over the target item.",
        returns="The queued proposal id, its masked description and the approval notice.",
        required_permission="MANAGEMENT",
        read_only=False,
        executor=_wrap_sync(sync_body),
    )


def _discovery_tool() -> ToolDefinition:
    """Build the mutable-fields discovery tool (genapi-style)."""
    return ToolDefinition(
        name="get_mutable_fields",
        title="Discover mutable fields",
        description=(
            "Field definitions of a mutable action type, resolved either from an item UUID "
            "(includes its current values) or from a data type name. Use it before proposing "
            "changes to know exactly which fields the proposal accepts."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "description": "Mutable action type. Defaults to provider.update.",
                },
                "target_uuid": {
                    "type": "string",
                    "description": "UUID of an existing item (returns its current values too).",
                },
                "for_type": {
                    "type": "string",
                    "description": "Data type name (e.g. the provider module type), when no UUID is at hand.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        access="Staff (the MCP endpoint gate).",
        returns="Ordered field definitions (name, type, label, choices, ...) and, with a UUID, the current values.",
        read_only=True,
        executor=_wrap_sync(_discovery_sync),
    )


def _list_tool() -> ToolDefinition:
    """Build the my-proposals listing tool."""
    return ToolDefinition(
        name="list_pending_actions",
        title="List my proposals",
        description=(
            "The proposals created with this identity, with their state. Approved and rejected "
            "proposals keep their outcome (result), so follow-up sessions can see what happened. "
            "Secret values are always redacted. Each item carries its proposal flow; decisions "
            "(rejected, cancelled, expired) happen at flow level. Only flows created within the "
            "last 30 days are returned (narrow it or move the window with the date filters)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": (
                        "Optional filter, matching the action or its flow: pending, locked, approved, "
                        "executing, executed, failed, skipped, revoked, rejected, cancelled or expired."
                    ),
                },
                "action_type": {
                    "type": "string",
                    "description": "Optional filter by action type (e.g. provider.update).",
                },
                "created_after": {
                    "type": "string",
                    "description": (
                        "Optional ISO date/datetime: only flows created at or after it. "
                        "Defaults to 30 days back."
                    ),
                },
                "created_before": {
                    "type": "string",
                    "description": "Optional ISO date/datetime: only flows created at or before it.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        access="Staff (only the proposals of the calling identity are returned).",
        returns="The list of proposals with state, masked changes and outcomes.",
        read_only=True,
        executor=_wrap_sync(_list_sync),
    )


def _update_tool() -> ToolDefinition:
    """Build the update-my-proposal tool (re-propose with new values)."""
    return ToolDefinition(
        name="update_pending_action",
        title="Update my proposal",
        description=(
            "Replace the values of one of your own pending proposals (iterative refinement), "
            "or revive one of your EXPIRED proposals (within its grace window): the whole flow "
            "returns to pending with a fresh expiration. The proposal is re-validated and "
            "re-based on the current state. It does NOT apply anything."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Proposal id."},
                "values": {
                    "type": "object",
                    "description": "New complete set of field values.",
                    "additionalProperties": True,
                },
                "expires_in_hours": {
                    "type": "integer",
                    "description": (
                        "Optional new expiration window in hours (1..720, default 7 days). "
                        "Used when reviving an expired proposal."
                    ),
                },
            },
            "required": ["id", "values"],
            "additionalProperties": False,
        },
        access="Only the owner of the proposal, while it is pending.",
        returns="The updated proposal id and its masked description.",
        read_only=False,
        executor=_wrap_sync(_update_sync),
    )


def _cancel_tool() -> ToolDefinition:
    """Build the cancel-my-proposal tool."""
    return ToolDefinition(
        name="cancel_pending_action",
        title="Cancel my proposal",
        description="Withdraw one of your own pending proposals.",
        input_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Proposal id."},
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        access="Only the owner of the proposal, while it is pending.",
        returns="The cancelled proposal id and state.",
        read_only=False,
        executor=_wrap_sync(_cancel_sync),
    )


def register_mutability_tools(catalog: Catalog) -> None:
    """Register the proposal tools of the registry plus the shared ones."""
    catalog.add_tool(_discovery_tool())
    catalog.add_tool(_list_tool())
    catalog.add_tool(_update_tool())
    catalog.add_tool(_cancel_tool())
    for action_type_cls in registry.all_types():
        catalog.add_tool(_propose_tool(action_type_cls()))
