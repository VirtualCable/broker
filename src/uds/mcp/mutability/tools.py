"""MCP tools for the supervised mutability.

One proposal tool per registered ``MutableActionType`` (generated from
the registry), one discovery tool and the three management tools the
agent uses to iterate on its own proposals. Nothing here applies a
change: proposals wait until an administrator approves them from the
administration interface (phase 3 of doc/plan/mcp-mutability.md).

Like the REST proxy, every executor runs its (synchronous) ORM work
inside a ``sync_to_async`` boundary, because Django ORM access is not
allowed from the MCP async event loop.
"""

import collections.abc
import typing

from asgiref.sync import sync_to_async

from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.mcp import FlowStatus
from uds.core.util import permissions

from ..catalog import Catalog, ToolDefinition
from . import registry
from .base import JsonObject, REDACTED
from .store import FlowStore, MutabilityError, NotActionOwner

JsonDict = dict[str, typing.Any]

ExecutorSync = collections.abc.Callable[[JsonObject, typing.Any], typing.Any]


def _request_user(request: typing.Any) -> typing.Any:
    """The UDS user behind the MCP request."""
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "uuid", None):
        raise rest_exceptions.AccessDenied()
    return user


def _agent_name(request: typing.Any) -> str:
    """Best-effort agent identification, informational only."""
    meta: dict[str, typing.Any] = getattr(request, "META", None) or {}
    return str(meta.get("HTTP_USER_AGENT", "") or "")


def _own_flow_action(store: FlowStore, user: typing.Any, action_id: str) -> tuple[typing.Any, typing.Any]:
    """The action with this id and its flow, both owned by ``user``."""
    action = store.get_action(action_id)
    if action is None:
        raise ValueError(f"No pending action with id {action_id}")
    flow = action.flow
    if flow.owner is None or str(flow.owner.uuid) != str(user.uuid):
        raise NotActionOwner(f"Action {action_id} does not belong to user {user.uuid}")
    return action, flow


def action_type_of(action: typing.Any) -> registry.MutableActionType:
    found = registry.get(action.action_type)
    if found is None:
        raise MutabilityError(f"Unknown action type {action.action_type}")
    return found


def _wrap_sync(sync_body: ExecutorSync) -> collections.abc.Callable[..., typing.Any]:
    """Executor wrapper: run the sync body off the event loop."""

    async def executor(arguments: JsonObject, request: typing.Any = None) -> typing.Any:
        return await sync_to_async(sync_body, thread_sensitive=True)(arguments, request)

    return executor


def _propose_sync(
    action_type: registry.MutableActionType,
    arguments: JsonObject,
    request: typing.Any,
) -> JsonDict:
    user = _request_user(request)
    target_uuid = str(arguments.get("target_uuid", "") or "")
    if not target_uuid.strip():
        raise ValueError("target_uuid is required")
    values = arguments.get("values")
    if not isinstance(values, dict) or not values:
        raise ValueError("values is required and must be a non-empty object")
    values = typing.cast("dict[str, typing.Any]", values)
    justification = str(arguments.get("justification", "") or "")

    target = action_type.resolve_target(target_uuid)
    for_type = action_type.for_type_of(target)
    if not permissions.has_access(user, target, types.permissions.PermissionType.MANAGEMENT):
        raise rest_exceptions.AccessDenied()

    errors = action_type.validate_values(for_type, values)
    if errors:
        raise ValueError("; ".join(errors))

    store = FlowStore()
    flow = store.create_flow(
        owner=user,
        agent=_agent_name(request),
        name=action_type.title,
        justification=justification,
    )
    base_values, base_etag = action_type.snapshot_and_fingerprint(target, values)
    action = store.add_action(
        flow,
        action_type=action_type.type_id,
        target_uuid=target_uuid,
        values=dict(values),
        base_values=base_values,
        base_etag=base_etag,
        justification=justification,
    )
    return {
        "id": action.uuid,
        "flow_id": flow.uuid,
        "status": action.status,
        "message": "Proposal queued. It does NOT take effect until an administrator approves it.",
        "proposal": action_type.describe(action),
    }


def _discovery_sync(arguments: JsonObject, request: typing.Any) -> JsonDict:
    _request_user(request)
    type_id = str(arguments.get("action_type", "") or "provider.update")
    action_type = registry.get(type_id)
    if action_type is None:
        known = ", ".join(t.type_id for t in registry.all_types())
        raise ValueError(f"Unknown action type {type_id} (known: {known})")

    target_uuid = str(arguments.get("target_uuid", "") or "")
    for_type = str(arguments.get("for_type", "") or "")
    result: JsonDict = {"action_type": type_id}
    if target_uuid.strip():
        target = action_type.resolve_target(target_uuid)
        for_type = action_type.for_type_of(target)
        result["target_uuid"] = target_uuid
        result["for_type"] = for_type
        fields = action_type.field_definitions(for_type)
        result["fields"] = fields
        result["current_values"] = action_type.snapshot_values(target, [d["name"] for d in fields])
    elif for_type.strip():
        result["for_type"] = for_type
        result["fields"] = action_type.field_definitions(for_type)
    else:
        raise ValueError("Provide either target_uuid or for_type")
    return result


def _list_sync(arguments: JsonObject, request: typing.Any) -> JsonDict:
    user = _request_user(request)
    store = FlowStore()
    status_raw = str(arguments.get("status", "") or "")
    type_filter = arguments.get("action_type")
    action_type = registry.get(str(type_filter)) if type_filter else None
    result: list[JsonDict] = []
    for action in store.list_actions(
        owner_uuid=str(user.uuid),
        action_type=action_type.type_id if action_type else None,
    ):
        # Status filter matches either the action lifecycle or the
        # decision taken on its flow (cancelled/rejected/expired are
        # flow-level states).
        if status_raw and action.status != status_raw and action.flow.status != status_raw:
            continue
        item: JsonDict = {
            "id": action.uuid,
            "flow": {
                "id": action.flow.uuid,
                "name": action.flow.name,
                "status": action.flow.status,
            },
            "type": action.action_type,
            "target": action.target_uuid,
            "status": action.status,
            "created": action.created.isoformat(),
            "result": action.properties.get("result"),
        }
        if action.flow.status == FlowStatus.PENDING:
            try:
                item["proposal"] = action_type_of(action).describe(action)
            except Exception:
                item["proposal"] = {"changes": {name: REDACTED for name in sorted(action.values)}}
        else:
            item["decided_by"] = action.flow.properties.get("decided_by")
        result.append(item)
    return {"items": result, "count": len(result)}


def _update_sync(arguments: JsonObject, request: typing.Any) -> JsonDict:
    user = _request_user(request)
    action_id = str(arguments.get("id", "") or "")
    if not action_id.strip():
        raise ValueError("id is required")
    values = arguments.get("values")
    if not isinstance(values, dict) or not values:
        raise ValueError("values is required and must be a non-empty object")
    values = typing.cast("dict[str, typing.Any]", values)

    store = FlowStore()
    action, flow = _own_flow_action(store, user, action_id)
    if flow.status != FlowStatus.PENDING:
        # Decided flows are terminal: nobody mutates them, not even
        # the owner (a change of mind means proposing a new action).
        raise ValueError(f"Action {action_id} is {flow.status}, only pending actions can be updated")
    action_type = action_type_of(action)

    target = action_type.resolve_target(action.target_uuid)
    for_type = action_type.for_type_of(target)
    errors = action_type.validate_values(for_type, values)
    if errors:
        raise ValueError("; ".join(errors))

    # Full replacement of the proposal, re-based on the current state.
    base_values, base_etag = action_type.snapshot_and_fingerprint(target, values)
    action.values = dict(values)
    action.base_values = base_values
    action.base_etag = base_etag
    action.save(update_fields=["values", "base_values", "base_etag"])
    return {
        "id": action.uuid,
        "flow_id": flow.uuid,
        "status": action.status,
        "message": "Proposal updated. It does NOT take effect until an administrator approves it.",
        "proposal": action_type.describe(action),
    }


def _cancel_sync(arguments: JsonObject, request: typing.Any) -> JsonDict:
    user = _request_user(request)
    action_id = str(arguments.get("id", "") or "")
    if not action_id.strip():
        raise ValueError("id is required")
    store = FlowStore()
    action, flow = _own_flow_action(store, user, action_id)
    # Decisions are flow-level: cancelling withdraws the whole proposal
    store.cancel_flow(flow, actor_uuid=str(user.uuid))
    action.refresh_from_db()
    return {
        "id": action.uuid,
        "flow_id": flow.uuid,
        "status": action.status,
        "flow_status": flow.status,
        "message": "Proposal cancelled.",
    }


def _propose_tool(action_type: registry.MutableActionType) -> ToolDefinition:
    """Build the proposal tool of one registered action type."""

    def sync_body(arguments: JsonObject, request: typing.Any) -> JsonDict:
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
            "(rejected, cancelled, expired) happen at flow level."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": (
                        "Optional filter, matching the action or its flow: pending, approved, executing, "
                        "executed, failed, skipped, rejected, cancelled or expired."
                    ),
                },
                "action_type": {
                    "type": "string",
                    "description": "Optional filter by action type (e.g. provider.update).",
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
            "Replace the values of one of your own pending proposals (iterative refinement). "
            "The proposal is re-validated and re-based on the current state. It does NOT apply anything."
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
    for action_type in registry.all_types():
        catalog.add_tool(_propose_tool(action_type))
