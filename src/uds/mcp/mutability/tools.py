"""MCP tools for the supervised mutability.

One proposal tool per registered ``MutableActionType`` (generated from
the registry), one discovery tool and the flow lifecycle tools the agent
uses to compose its own proposals. Nothing here applies a change: a
submitted flow waits until an administrator approves it from the
administration interface (phase 3 of doc/plan/mcp-mutability.md).

The lifecycle is flow-centric and mirrors the owner REST surface
(``/flows/own``): open a draft (``create_flow``), append actions to it
(``propose_*``), refine an action while it is still a draft
(``update_flow_action``), then close the draft for review
(``submit_flow``). A draft is the agent's private space: administrators
cannot see or lock it until it is submitted, and once submitted neither
the agent can edit it nor can it be reopened (a change means a new
flow). ``cancel_flow`` withdraws a flow the owner still controls (a
draft, or a submitted one the administrator has not locked yet).

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
from uds.core.consts.mcp import CREATE_TARGET_UUID
from uds.mutability.base import ActionOperation, JsonObject, REDACTED

JsonDict = dict[str, typing.Any]

ExecutorSync = collections.abc.Callable[[JsonObject, ExtendedHttpRequestWithUser], typing.Any]

_APPEND_MESSAGE: typing.Final[str] = (
    "Action added to the draft flow. It does NOT take effect until you submit the flow "
    "and an administrator approves it."
)
_UPDATE_MESSAGE: typing.Final[str] = "Draft action updated. Submit the flow when it is ready for review."


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
    """Serialize the item a detail write operation answers with.

    Detail writes answer with the saved item itself (the same shape a GET
    returns), so here it is only the dataclass-to-dict step the HTTP layer
    would perform at render time.
    """
    return typing.cast("JsonDict", _as_dict(response))


def _registry_type(type_id: str) -> registry.MutableActionType:
    found = registry.get(type_id)
    if found is None:
        raise ValueError(f"Unknown action type {type_id}")
    return found()


def _flow_summary(flow: JsonDict) -> JsonDict:
    """The compact flow view shared by the lifecycle responses."""
    return {
        "id": flow["id"],
        "name": flow["name"],
        "status": flow["status"],
        "actions": flow.get("actions_count"),
        "due_date": flow["due_date"].isoformat() if flow.get("due_date") else None,
    }


def _own_action_index(
    request: ExtendedHttpRequestWithUser, statuses: collections.abc.Container[str]
) -> dict[str, tuple[str, JsonDict]]:
    """Index of the caller's actions: id -> (flow_id, item), flow status filtered.

    The index is built from the REST surface, so ownership is already
    enforced there; ``statuses`` selects which flow states are eligible
    (a draft flow's actions can still be refined).
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


def _create_flow_sync(arguments: JsonObject, request: ExtendedHttpRequestWithUser) -> JsonDict:
    """Open a draft flow: the composing space for one batch of changes."""
    _request_user(request)
    flow = typing.cast(
        "JsonDict",
        _as_dict(
            _flows_rest(
                request,
                "post",
                {
                    "name": str(arguments.get("name", "") or ""),
                    "justification": str(arguments.get("justification", "") or ""),
                },
            )
        ),
    )
    return {
        "flow_id": flow["id"],
        "status": flow["status"],
        "message": "Draft flow opened. Add actions with the propose_* tools, then submit_flow to queue it.",
        "flow": _flow_summary(flow),
    }


def _submit_flow_sync(arguments: JsonObject, request: ExtendedHttpRequestWithUser) -> JsonDict:
    """Close a draft flow and hand it to the administrator for review."""
    _request_user(request)
    flow_id = str(arguments.get("flow_id", "") or "")
    if not flow_id.strip():
        raise ValueError("flow_id is required")
    flow = typing.cast(
        "JsonDict",
        _as_dict(
            _flows_rest(
                request,
                "post",
                {"expires_in_hours": arguments.get("expires_in_hours", "")},
                flow_id,
                "submit",
            )
        ),
    )
    return {
        "flow_id": flow["id"],
        "status": flow["status"],
        "message": (
            "Flow submitted. It does NOT take effect until an administrator approves it. "
            "It can no longer be edited; cancel it (before the administrator locks it) "
            "and open a new one to change anything."
        ),
        "flow": _flow_summary(flow),
    }


def _propose_sync(
    action_type: registry.MutableActionType,
    arguments: JsonObject,
    request: ExtendedHttpRequestWithUser,
) -> JsonDict:
    _request_user(request)
    flow_id = str(arguments.get("flow_id", "") or "")
    if not flow_id.strip():
        raise ValueError("flow_id is required: open a flow with create_flow first")
    target_uuid = str(arguments.get("target_uuid", "") or "")
    is_root_create = action_type.operation is ActionOperation.CREATE and not action_type.create_needs_parent
    if not target_uuid.strip():
        if is_root_create:
            target_uuid = CREATE_TARGET_UUID  # root creation: no target yet
        else:
            raise ValueError("target_uuid is required")
    values = arguments.get("values")
    if not isinstance(values, dict) or not values:
        # Deletion proposals carry no fields (the target itself is the
        # payload), so an omitted or empty values object is valid there
        # and only there; every other verb needs real changes.
        if action_type.operation is not ActionOperation.DELETE:
            raise ValueError("values is required and must be a non-empty object")
        values = {}
    values = typing.cast("dict[str, typing.Any]", values)
    justification = str(arguments.get("justification", "") or "")

    # The action is appended to a draft flow the caller already owns,
    # through the detail surface (validation, MANAGEMENT over the target,
    # fresh CAS base and caps are the REST ones). A non-draft or foreign
    # flow is refused there with a clean error.
    action = _detail_result(
        _flows_rest(
            request,
            "post",
            {
                "action_type": action_type.full_id,
                "target_uuid": target_uuid,
                "values": values,
                "justification": justification,
            },
            flow_id,
            "actions",
        )
    )
    return {
        "id": action["id"],
        "flow_id": flow_id,
        "status": action["status"],
        "message": _APPEND_MESSAGE,
        "proposal": _describe_item(action_type, action),
    }


def _discovery_sync(arguments: JsonObject, request: ExtendedHttpRequestWithUser) -> JsonDict:
    _request_user(request)
    type_id = str(arguments.get("action_type", "") or "provider.update")
    found = registry.get(type_id)
    if found is None:
        known = ", ".join(registry.all_type_ids())
        raise ValueError(f"Unknown action type {type_id} (known: {known})")
    action_type = found()

    target_uuid = str(arguments.get("target_uuid", "") or "")
    for_type = str(arguments.get("for_type", "") or "")
    result: JsonDict = {"action_type": type_id}
    if action_type.operation is ActionOperation.CREATE:
        # Creation surface: the fields depend on the declared subtype
        # (values["data_type"]), not on a live target. Detail creations
        # build their gui from the parent item. A gallery-less family
        # (a pool, a network) has no subtype to declare: its single gui
        # is reached with the family root as for_type.
        if not for_type.strip():
            if not action_type.create_has_gallery:
                for_type = type_id.partition(".")[0]
            else:
                raise ValueError(
                    f"{type_id} requires for_type: the subtype the proposal creates "
                    "(use get_creatable_types to list them)"
                )
        if action_type.create_needs_parent:
            if not target_uuid.strip():
                raise ValueError(f"{type_id} requires target_uuid: the uuid of the parent item")
            # The uuid of a detail creation is the PARENT's: resolve it
            # through the container hook (resolve_target looks up the
            # family's own entity, which does not exist yet), exactly
            # like the REST flows create path does.
            target = action_type.resolve_create_parent(target_uuid)
            result["target_uuid"] = target_uuid
        else:
            target = None
        result["for_type"] = for_type
        result["fields"] = action_type.create_field_definitions(for_type, target)
        return result
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
            # state of its flow (draft, cancelled, rejected, expired are
            # flow-level; only a draft/locked flow's actions can move).
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
            # Live masked view of the proposal while the owner still
            # controls it (draft) or the administrator holds it queued
            # (pending/locked). Decided flows only show the outcome.
            if flow["status"] in (FlowStatus.DRAFT, FlowStatus.PENDING, FlowStatus.LOCKED):
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

    # Only actions of a draft flow can be refined; a submitted (or
    # locked, or decided) flow is final. The index is built from the REST
    # surface, so ownership is already enforced there.
    found = _own_action_index(request, (FlowStatus.DRAFT,)).get(action_id)
    if found is None:
        raise ValueError(f"No draft action with id {action_id} (actions can only be edited before submitting)")
    flow_id, item = found

    # Full replacement of the proposal, re-based on the current state
    # (the REST detail PUT validates and snapshots the new base).
    action = _detail_result(_flows_rest(request, "put", {"values": values}, flow_id, "actions", action_id))
    return {
        "id": action_id,
        "flow_id": flow_id,
        "status": action["status"],
        "message": _UPDATE_MESSAGE,
        "proposal": _describe_item(_registry_type(str(item["action_type"])), action),
    }


def _cancel_sync(arguments: JsonObject, request: ExtendedHttpRequestWithUser) -> JsonDict:
    _request_user(request)
    flow_id = str(arguments.get("flow_id", "") or "")
    if not flow_id.strip():
        raise ValueError("flow_id is required")
    # Decisions are flow-level: cancelling withdraws the whole proposal
    # (a draft the agent is composing, or a submitted flow the
    # administrator has not locked yet). A locked flow is refused there.
    flow = typing.cast("JsonDict", _as_dict(_flows_rest(request, "delete", {}, flow_id)))
    return {
        "flow_id": flow["id"],
        "flow_status": flow["status"],
        "message": "Flow cancelled.",
        "flow": _flow_summary(flow),
    }


def _propose_tool(action_type: registry.MutableActionType) -> ToolDefinition:
    """Build the proposal tool of one registered action type."""

    def sync_body(arguments: JsonObject, request: ExtendedHttpRequestWithUser) -> JsonDict:
        return _propose_sync(action_type, arguments, request)

    is_root_create = action_type.operation is ActionOperation.CREATE and not action_type.create_needs_parent
    is_delete = action_type.operation is ActionOperation.DELETE
    if action_type.operation is ActionOperation.CREATE:
        target_description = (
            "UUID of the PARENT item the new entity is created into."
            if action_type.create_needs_parent
            else "Must be omitted for a creation: there is no target yet."
        )
        required = ["flow_id", "values"] if is_root_create else ["flow_id", "target_uuid", "values"]
    elif is_delete:
        # A deletion has no field payload: the target itself is what
        # disappears, so values is not part of the contract here.
        target_description = "UUID of the item to delete."
        required = ["flow_id", "target_uuid"]
    else:
        target_description = "UUID of the item to mutate."
        required = ["flow_id", "target_uuid", "values"]

    return ToolDefinition(
        name=f"propose_{action_type.full_id.replace('.', '_')}",
        title=action_type.tool_title(),
        description=action_type.tool_description(),
        input_schema={
            "type": "object",
            "properties": {
                "flow_id": {
                    "type": "string",
                    "description": (
                        "Draft flow to add this action to (from create_flow). "
                        "A flow can only receive actions while it is still a draft."
                    ),
                },
                "target_uuid": {
                    "type": "string",
                    "description": target_description,
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
            },
            "required": required,
            "additionalProperties": False,
        },
        access="Staff with MANAGEMENT permission over the target item.",
        returns="The added action id, its masked description and the draft-flow notice.",
        examples=(
            "Request: {\"flow_id\": \"<draft-uuid>\", \"values\": {\"username\": \"root@pam\", "
            "\"password\": \"<password>\", \"host\": \"192.168.1.151\", \"port\": 8006, "
            "\"verify_ssl\": false}, \"justification\": \"Set up Proxmox provider David wants to demo.\"}\n"
            "Expected response: {\"id\": \"<action-uuid>\", \"action_type\": \"provider.create\", "
            "\"flow\": {\"id\": \"<draft-uuid>\", \"status\": \"draft\"}, \"target_uuid\": null, "
            "\"fields\": {\"username\": \"root@pam\", \"password\": \"***\", ...}}",
        ),
        required_permission="MANAGEMENT",
        read_only=False,
        executor=_wrap_sync(sync_body),
    )


def _create_flow_tool() -> ToolDefinition:
    """Build the open-a-draft-flow tool."""
    return ToolDefinition(
        name="create_flow",
        title="Open a draft flow",
        description=(
            "Open an empty draft flow to compose a batch of related changes. The flow is the "
            "scope the administrator reviews: call ``propose_*`` once per change (create / "
            "update / delete) and finish with ``submit_flow``. Drafts are private to the "
            "caller until submitted — no administrator can see them. Unsubmitted drafts are "
            "auto-discarded after a short window, so only open a draft when the batch of "
            "changes is ready to write. Drafts that depend on each other must live in the "
            "same flow so the administrator approves them together."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Short, human-readable title for the whole batch of changes "
                        "(e.g. 'Create Proxmox provider and 2 pools'). Shown in the admin "
                        "queue and in ``list_flows_own`` output; keep it under 80 chars."
                    ),
                },
                "justification": {
                    "type": "string",
                    "description": (
                        "Plain-language reason the administrator should approve this batch. "
                        "Mention the user-visible impact, the requester (if known) and any "
                        "rollback plan. Required for traceability, even when the flow is short."
                    ),
                },
            },
            "required": ["name", "justification"],
            "additionalProperties": False,
        },
        access="Staff (the MCP endpoint gate).",
        returns="The new draft flow id and its summary (id, name, status='draft', due_date, actions_count=0).",
        examples=(
            "Request: {\"name\": \"Create Proxmox provider and 2 pools\", \"justification\": \"Bring up the Proxmox provider David wants to test; pool W11 and pool W11-SSO.\"}\n"
            "Expected response: {\"id\": \"<new-draft-uuid>\", \"name\": \"...\", \"status\": \"draft\", \"due_date\": \"2026-10-16T18:00:00Z\", \"actions\": 0}",
        ),
        read_only=False,
        executor=_wrap_sync(_create_flow_sync),
    )


def _submit_flow_tool() -> ToolDefinition:
    """Build the submit-a-flow tool."""
    return ToolDefinition(
        name="submit_flow",
        title="Submit a flow for review",
        description=(
            "Close one of your draft flows and queue it for the administrator. From this "
            "moment the flow is final: it can no longer be edited, and it does nothing until "
            "an administrator approves it. To change something afterwards, cancel it (if not "
            "yet locked) and open a new flow. Submission is the only way to get a draft in "
            "front of a human reviewer, so check every action (type, target_uuid, fields) "
            "before calling this tool."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "flow_id": {"type": "string", "description": "Draft flow to submit."},
                "expires_in_hours": {
                    "type": "integer",
                    "description": (
                        "Hours you expect an administrator to need to resolve this proposal "
                        "(1..720, default 7 days). The flow auto-expires after it, so declare "
                        "a realistic window. The countdown starts now, at submit."
                    ),
                },
            },
            "required": ["flow_id"],
            "additionalProperties": False,
        },
        access="Only the owner of the draft flow.",
        returns="The submitted flow id, its pending status and the approval notice.",
        examples=(
            "Request: {\"flow_id\": \"<draft-uuid>\", \"expires_in_hours\": 48}\n"
            "Expected response: {\"id\": \"<draft-uuid>\", \"name\": \"...\", \"status\": \"pending\", \"due_date\": \"2026-10-18T16:00:00Z\"}",
        ),
        read_only=False,
        executor=_wrap_sync(_submit_flow_sync),
    )


def _discovery_tool() -> ToolDefinition:
    """Build the mutable-fields discovery tool (genapi-style)."""
    return ToolDefinition(
        name="get_mutable_fields",
        title="Discover mutable fields",
        description=(
            "Field definitions of a mutable action type, resolved either from an item UUID "
            "(includes its current values) or from a data type name. Use it before proposing "
            "changes to know exactly which fields the proposal accepts, their types, the "
            "human-readable label, and any choice list. The tool is the genapi-style entry "
            "point for action shapes — pick this before constructing any ``propose_*`` call "
            "so the model does not invent field names."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "description": (
                        "Mutable action type identifier (e.g. ``provider.create``, "
                        "``provider.update``, ``service.update``). Defaults to "
                        "``provider.update`` when omitted. Use ``list_flow_actions`` or "
                        "the registry output to discover available types in this UDS install."
                    ),
                },
                "target_uuid": {
                    "type": "string",
                    "description": (
                        "UUID of an existing item. When provided, the response also carries "
                        "the current values of the listed fields, so you can compose a "
                        "differential update without guessing. For create actions, pass the "
                        "parent UUID (the container that will own the new item)."
                    ),
                },
                "for_type": {
                    "type": "string",
                    "description": (
                        "Data type name (e.g. ``IPServiceProvider`` for a Proxmox "
                        "provider, or any subclass declared in the provider module). "
                        "Required when ``target_uuid`` is empty and the action_type has "
                        "no single canonical type."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        access="Staff (the MCP endpoint gate).",
        returns=(
            "An object with ``action_type``, ``for_type``, and ``fields`` (list of "
            "field definitions in their render order). When ``target_uuid`` is provided, "
            "the response also carries ``target_uuid`` and ``current_values``."
        ),
        examples=(
            "Request: {\"action_type\": \"provider.update\", \"target_uuid\": \"<provider-uuid>\"}\n"
            "Expected response: {\"action_type\": \"provider.update\", \"target_uuid\": \"<provider-uuid>\", "
            "\"for_type\": \"IPServiceProvider\", \"fields\": [{\"name\":\"username\",\"type\":\"text\",\"label\":\"Username\",\"required\":true}, ...], "
            "\"current_values\": {\"username\": \"root@pam\", ...}}",
            "Request: {\"action_type\": \"provider.create\", \"for_type\": \"IPServiceProvider\"}\n"
            "Expected response: {\"action_type\": \"provider.create\", \"for_type\": \"IPServiceProvider\", "
            "\"fields\": [{\"name\":\"username\", ...}, {\"name\":\"password\",\"type\":\"password\",\"label\":\"Password\",\"required\":true}, ...]}",
        ),
        read_only=True,
        executor=_wrap_sync(_discovery_sync),
    )


def _list_tool() -> ToolDefinition:
    """Build the my-actions listing tool."""
    return ToolDefinition(
        name="list_flow_actions",
        title="List my flow actions",
        description=(
            "The actions created with this identity, grouped by their flow state. Draft actions "
            "are the ones you can still edit; submitted ones keep their outcome (result), so "
            "follow-up sessions can see what happened. Secret values are always redacted. Each "
            "item carries its flow (id and status); decisions (rejected, cancelled, expired) "
            "happen at flow level. Only flows created within the last 30 days are returned "
            "(narrow or move the window with the date filters)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": (
                        "Optional filter, matching the action or its flow: draft, pending, locked, "
                        "approved, executing, executed, failed, skipped, revoked, rejected, "
                        "cancelled or expired."
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
        access="Staff (only the actions of the calling identity are returned).",
        returns="The list of actions with their flow, state, masked changes and outcomes.",
        examples=(
            "Request: {\"status\": \"draft\", \"top\": 10}\n"
            "Expected response: {\"items\": [{\"id\": \"<action-uuid>\", \"flow\": {\"id\": \"<flow-uuid>\", \"status\": \"draft\"}, "
            "\"action_type\": \"provider.create\", \"fields\": {\"username\": \"root@pam\", ...}, ...}], \"count\": N}",
            "Request: {\"status\": \"failed\", \"created_after\": \"2026-10-01T00:00:00Z\"}\n"
            "Expected response: actions whose flow ended in failure since the date, with "
            "``result`` and ``run_note`` filled in (no secrets).",
        ),
        read_only=True,
        executor=_wrap_sync(_list_sync),
    )


def _update_tool() -> ToolDefinition:
    """Build the refine-a-draft-action tool."""
    return ToolDefinition(
        name="update_flow_action",
        title="Refine a draft action",
        description=(
            "Replace the values of one of your own draft actions (iterative refinement before "
            "submitting). Only actions of a draft flow can be edited: once the flow is "
            "submitted it is final and a change means a new flow. The action is re-validated "
            "and re-based on the current state. It does NOT apply anything. The ``values`` "
            "object must match the field set returned by ``get_mutable_fields`` for this action "
            "type, otherwise the broker rejects the call with a 400 and the action returns to "
            "its previous state."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": (
                        "Action id returned by ``list_flow_actions`` or by the ``propose_*`` "
                        "call that created it. The action must belong to a flow whose status "
                        "is still ``draft``; otherwise the broker returns a conflict error."
                    ),
                },
                "values": {
                    "type": "object",
                    "description": (
                        "New complete set of field values, keyed by field name. Must follow the "
                        "same shape as ``get_mutable_fields(...)['fields']`` for this "
                        "action_type. Sensitive fields (e.g. passwords) are accepted but stored "
                        "encrypted and never echoed back in subsequent reads."
                    ),
                    "additionalProperties": True,
                },
            },
            "required": ["id", "values"],
            "additionalProperties": False,
        },
        access="Only the owner, while the flow is still a draft.",
        returns="The updated action id and its masked description.",
        examples=(
            "Request: {\"id\": \"<action-uuid>\", \"values\": {\"username\": \"ops@pam\", \"password\": \"<new-value>\", \"host\": \"192.168.1.151\"}}\n"
            "Expected response: {\"id\": \"<action-uuid>\", \"action_type\": \"provider.update\", "
            "\"fields\": {\"username\": \"ops@pam\", \"password\": \"***\", \"host\": \"192.168.1.151\"}}",
        ),
        read_only=False,
        executor=_wrap_sync(_update_sync),
    )


def _cancel_tool() -> ToolDefinition:
    """Build the cancel-my-flow tool."""
    return ToolDefinition(
        name="cancel_flow",
        title="Cancel my flow",
        description=(
            "Withdraw one of your own flows: a draft you are composing, or a submitted flow "
            "the administrator has not locked yet. Once a flow is locked (in review) it can "
            "no longer be cancelled here. Cancelling a flow removes it from the administrator "
            "queue (pending) or the caller's drafts; nothing is applied. Use this to retire a "
            "proposal that became obsolete before approval — for partial changes, "
            "open a new flow instead of resubmitting the cancelled one."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "flow_id": {
                    "type": "string",
                    "description": (
                        "Id of the flow to withdraw. Must be either your own draft (any time) "
                        "or your own submitted flow that has not been locked by an "
                        "administrator yet."
                    ),
                },
            },
            "required": ["flow_id"],
            "additionalProperties": False,
        },
        access="Only the owner, while the flow is a draft or pending (not yet locked).",
        returns="The cancelled flow id and state.",
        examples=(
            "Request: {\"flow_id\": \"<draft-or-pending-uuid>\"}\n"
            "Expected response: {\"id\": \"<flow-uuid>\", \"status\": \"cancelled\"}",
        ),
        read_only=False,
        executor=_wrap_sync(_cancel_sync),
    )


def register_mutability_tools(catalog: Catalog) -> None:
    """Register the proposal tools of the registry plus the shared ones."""
    catalog.add_tool(_discovery_tool())
    catalog.add_tool(_create_flow_tool())
    catalog.add_tool(_submit_flow_tool())
    catalog.add_tool(_list_tool())
    catalog.add_tool(_update_tool())
    catalog.add_tool(_cancel_tool())
    for binding in registry.all_bindings():
        catalog.add_tool(_propose_tool(binding.factory()))
