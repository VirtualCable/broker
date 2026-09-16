"""``servicepool.action.set``: set the scheduled calendar actions of a pool.

Relation-like action type over ``CalendarAction`` rows (option A:
``actions`` describes the FINAL set, never a delta). Unlike the other
whole-set families there is no natural key for a row (a pool may schedule
the same action on the same calendar twice), so a row is identified by
its uuid: proposed rows carrying a ``uuid`` edit live ones, rows without
it are created, and live rows missing from the set are removed. The diff
is applied through the canonical ``services_pools/{uuid}/actions`` detail
surface.

The action payload is self-describing: each ``CALENDAR_ACTION_DICT``
entry declares its parameters (numeric / bool / transport / group), and
``params`` is validated against that schema, so an agent learns the exact
shape from get_mutable_fields without guessing. The immediate-execution
custom method of the REST detail is intentionally NOT exposed: it is an
admin helper, not something a proposal should be able to trigger.
"""

import collections.abc
import hashlib
import json
import typing
from types import SimpleNamespace as _Namespace

from asgiref.sync import sync_to_async
from django.db import models as db_models

from uds import models
from uds.core import consts, types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.core.util.model import process_uuid
from uds.mcp.rest_proxy import RestProxy, RestTarget
from uds.REST.methods.op_calendars import ActionsCalendars
from uds.REST.methods.services_pools import ServicesPools

from ... import base as mutability_base

JsonObject = dict[str, typing.Any]

_ACTION_ROW_KEYS: typing.Final[frozenset[str]] = frozenset(
    {"uuid", "calendar_id", "action", "at_start", "events_offset", "params"}
)
_ACTION_ROW_REQUIRED: typing.Final[frozenset[str]] = frozenset(
    {"calendar_id", "action", "at_start", "events_offset", "params"}
)

ActionRow = dict[str, typing.Any]
ActionOperation = tuple[types.rest.CustomMethodMethod, str | None, JsonObject]


def _action_param_specs(action_id: str) -> dict[str, JsonObject]:
    """Declared parameter schema of one action id (empty when none)."""
    definition = consts.calendar.CALENDAR_ACTION_DICT.get(action_id)
    if definition is None:
        return {}
    return {param["name"]: param for param in definition["params"]}


def _action_rows(pool: models.ServicePool) -> list[ActionRow]:
    """Live scheduled actions as canonical rows (sorted by row uuid).

    The row uuid IS the identity here (no natural key exists), so it is
    part of the projection and travels through the CAS fingerprint.
    """
    return [
        {
            "uuid": row.uuid,
            "calendar_id": row.calendar.uuid,
            "action": row.action,
            "at_start": row.at_start,
            "events_offset": row.events_offset,
            "params": json.loads(row.params) if row.params else {},
        }
        for row in pool.calendaraction_set.select_related("calendar").order_by("uuid")
    ]


def _available_action_ids(pool: models.ServicePool) -> list[str]:
    """Action ids currently valid for the pool (REST ``actions_list``).

    ``ServicesPools.actions_list`` only reads the pool; binding it to an
    empty namespace avoids instantiating the full handler (whose
    ``__init__`` resolves authentication).
    """
    shim = typing.cast(typing.Any, _Namespace())
    return [action["id"] for action in ServicesPools.actions_list(shim, pool)]


class ServicePoolActions(mutability_base.MutableActionType):
    """Proposal: set the complete desired scheduled calendar actions of a service pool."""

    type_id = "servicepool.action"

    title = "Propose service pool scheduled actions"
    description = (
        "Propose the complete desired set of scheduled calendar actions of a "
        "service pool. The 'actions' field is the FINAL set, not a delta: rows "
        "missing from it will be removed, rows without 'uuid' are created and "
        "rows carrying the uuid of a current one edit it. Each row is: calendar_id "
        "(uuid of an existing calendar), action (id from the pool's currently "
        "available actions), at_start (boolean, relative to the calendar start or "
        "end), events_offset (integer minutes, negative = before) and params "
        "(object; its schema depends on the action, see get_mutable_fields). Use "
        "get_servicepool_action to see the current actions with their uuids. An "
        "empty list removes every scheduled action. The proposal does NOT apply "
        "anything: it is queued until an administrator approves it."
    )
    handler = ServicesPools
    model = models.ServicePool
    noun = "Service pool"
    # Discovery without the pool uuid is useless here: both the valid
    # action ids and the current rows depend on the concrete pool.
    target_scoped_fields = True

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.ServicePool.objects.get(uuid__iexact=target_uuid)
        except models.ServicePool.DoesNotExist:
            raise rest_exceptions.NotFound("Service pool not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return "servicepool"

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        calendars: list[JsonObject] = [
            {"value": str(uuid), "label": name}
            for uuid, name in models.Calendar.objects.order_by("name").values_list("uuid", "name")
        ]
        return [
            {
                "name": "actions",
                "type": types.ui.FieldType.EDITABLELIST.value,
                "label": "Scheduled calendar actions",
                "tooltip": self._rows_tooltip(target),
                "secret": False,
                "row": {
                    "uuid": "row uuid to edit an existing action (omit to create)",
                    "calendar_id": "uuid of an existing calendar (choices)",
                    "action": "action id currently valid for this pool",
                    "at_start": "boolean (relative to the calendar event start)",
                    "events_offset": "integer minutes offset (negative = before)",
                    "params": "object, schema depends on the action",
                },
                "choices": calendars,
            }
        ]

    def _rows_tooltip(self, target: db_models.Model | None) -> str:
        """Row schema plus the parameter shape of every action valid now."""
        lines = [
            (
                "Complete desired set of scheduled calendar actions. Rows not listed are "
                "removed on approval; keep the uuid of a row to edit it, omit uuid to create. "
            )
        ]
        if isinstance(target, models.ServicePool):
            specs: list[str] = []
            for action_id in _available_action_ids(target):
                definition = consts.calendar.CALENDAR_ACTION_DICT[action_id]
                specs.append(
                    f"{action_id} = {definition['description']} [{_params_shape(_action_param_specs(action_id))}]"
                )
            if specs:
                lines.append("Valid actions and their params: " + " | ".join(specs))
        else:
            lines.append(
                "Each row params schema depends on the action; call get_mutable_fields on the pool to see them."
            )
        return "".join(lines)

    @typing.override
    def validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        errors = super().validate_values(for_type, values, target)
        flat = self.flatten_values(values)
        if "actions" not in flat:
            errors.append("field actions is required (the complete desired set; use [] to remove all)")
            return errors
        errors.extend(self._validate_rows(flat["actions"], target))
        return errors

    def _validate_rows(self, rows: typing.Any, target: db_models.Model | None) -> list[str]:
        if not isinstance(rows, (list, tuple)):
            return []  # base already flagged "must be a list"
        errors: list[str] = []
        rows_seq = typing.cast("collections.abc.Sequence[typing.Any]", rows)
        seen_uuids: set[str] = set()
        live_by_uuid: dict[str, ActionRow] = {}
        valid_actions: set[str] | None = None
        if isinstance(target, models.ServicePool):
            live_by_uuid = {row["uuid"]: row for row in _action_rows(target)}
            valid_actions = set(_available_action_ids(target))
        for index, row in enumerate(rows_seq):
            where = f"actions[{index}]"
            if not isinstance(row, dict):
                errors.append(
                    f"{where} must be an object with calendar_id, action, at_start, events_offset and params"
                )
                continue
            row_dict = typing.cast("dict[str, typing.Any]", row)
            unknown = sorted(set(row_dict) - _ACTION_ROW_KEYS)
            missing = sorted(_ACTION_ROW_REQUIRED - set(row_dict))
            if unknown:
                errors.append(
                    f"{where} has unknown keys: {', '.join(unknown)} (accepted: {', '.join(sorted(_ACTION_ROW_KEYS))})"
                )
            if missing:
                errors.append(f"{where} is missing keys: {', '.join(missing)}")
                continue
            errors.extend(self._validate_row(row_dict, where, target, live_by_uuid, seen_uuids, valid_actions))
        return errors

    def _validate_row(
        self,
        row_dict: "dict[str, typing.Any]",
        where: str,
        target: db_models.Model | None,
        live_by_uuid: dict[str, ActionRow],
        seen_uuids: set[str],
        valid_actions: set[str] | None,
    ) -> list[str]:
        errors: list[str] = []
        uuid_value = row_dict.get("uuid")
        editing_uuid: str | None = None
        if uuid_value is not None:
            error, normalized = _validate_uuid(uuid_value, f"{where}.uuid", "scheduled action row")
            if error:
                errors.append(error)
            elif typing.cast("str", normalized) in seen_uuids:
                errors.append(f"{where}.uuid {normalized} appears twice in the proposed set")
            else:
                seen_uuids.add(typing.cast("str", normalized))
                if target is None or normalized in live_by_uuid:
                    editing_uuid = typing.cast("str", normalized)
                else:
                    errors.append(
                        f"{where}.uuid {normalized} does not match a current scheduled action of this pool "
                        "(omit uuid to create a new one)"
                    )
        calendar_error, _ = _validate_calendar_id(row_dict.get("calendar_id"), where)
        if calendar_error:
            errors.append(calendar_error)
        action = row_dict.get("action")
        action_id: str | None = None
        if not isinstance(action, str) or action.upper() not in consts.calendar.CALENDAR_ACTION_DICT:
            errors.append(
                f"{where}.action must be one of the action ids valid for this pool (see this tool description)"
            )
        else:
            action_id = action.upper()
            if valid_actions is not None and action_id not in valid_actions:
                # Editing an already scheduled action whose type stopped
                # being valid (e.g. the pool got locked) is still allowed:
                # it must remain fixable and removable. Creating a new one
                # with it is not.
                editing_a_different_action = (
                    editing_uuid is not None and live_by_uuid[editing_uuid]["action"] != action_id
                )
                if target is None or editing_a_different_action or editing_uuid is None:
                    errors.append(
                        f"{where}.action {action_id} is not currently valid for this pool "
                        f"(available: {', '.join(sorted(valid_actions))})"
                    )
        if not isinstance(row_dict.get("at_start"), bool):
            errors.append(f"{where}.at_start must be a boolean")
        events_offset = row_dict.get("events_offset")
        if isinstance(events_offset, bool) or not isinstance(events_offset, int):
            errors.append(f"{where}.events_offset must be an integer (minutes)")
        if action_id is not None:
            errors.extend(_validate_params(action_id, row_dict.get("params"), where))
        return errors

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        return ["actions"]

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        pool = typing.cast(models.ServicePool, target)
        wanted = set(names)
        return {"actions": _action_rows(pool)} if "actions" in wanted else {}

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        rows = _action_rows(typing.cast(models.ServicePool, target))
        return hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()

    @typing.override
    def target_display_name(self, target: db_models.Model) -> str:
        return typing.cast(models.ServicePool, target).name

    # ------------------------------------------------------- tool text / read

    @typing.override
    def read_title(self) -> str:
        return "Read service pool scheduled actions"

    @typing.override
    def read_description(self) -> str:
        return (
            "Read the current scheduled calendar actions of one service pool: every row "
            "with its uuid (to reuse in a servicepool.action.set proposal), calendar uuid "
            "and name, action id and description, at_start, events_offset and params, plus "
            "the action ids currently valid for the pool. Applies immediately; it is a "
            "plain read, never a proposal."
        )

    @typing.override
    def read(self, target_uuid: str) -> JsonObject:
        result = super().read(target_uuid)
        pool = typing.cast(models.ServicePool, self.resolve_target(target_uuid))
        names = {str(uuid): name for uuid, name in models.Calendar.objects.values_list("uuid", "name")}
        descriptions = {
            action_id: str(definition["description"])
            for action_id, definition in consts.calendar.CALENDAR_ACTION_DICT.items()
        }
        result["current_actions"] = [
            {
                **row,
                "calendar_name": names.get(str(row["calendar_id"]), ""),
                "action_description": descriptions.get(typing.cast(str, row["action"]), ""),
            }
            for row in _action_rows(pool)
        ]
        result["available_actions"] = _available_action_ids(pool)
        return result

    # ---------------------------------------------------------- execution

    @typing.override
    async def op_set(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # Plan the diff in one sync boundary (target + live rows), then run
        # the REST operations. The whole-set CAS check (stale policy DENY)
        # already validated the live set against the approved base before
        # the executor got here.
        def _plan() -> tuple[str, list[ActionOperation]]:
            pool = typing.cast(models.ServicePool, self.resolve_target(action.target_uuid))
            rows = _action_rows(pool)
            current = {row["uuid"]: row for row in rows}
            proposed = typing.cast("list[ActionRow]", action.values["actions"])
            edited: set[str] = set()
            operations: list[ActionOperation] = []
            for row in proposed:
                payload: JsonObject = {
                    "calendar_id": process_uuid(typing.cast(str, row["calendar_id"])),
                    "action": typing.cast(str, row["action"]).upper(),
                    "at_start": typing.cast(bool, row["at_start"]),
                    "events_offset": typing.cast(int, row["events_offset"]),
                    "params": dict(typing.cast(JsonObject, row["params"])),
                }
                row_uuid = row.get("uuid")
                if row_uuid is None:
                    operations.append((types.rest.CustomMethodMethod.POST, None, payload))
                    continue
                key = process_uuid(typing.cast(str, row_uuid))
                if key not in current:
                    raise rest_exceptions.RequestError(
                        f'Service pool "{pool.name}" no longer has scheduled action {key} '
                        "(it was removed after the proposal was approved; re-read and propose again)"
                    )
                edited.add(key)
                live = current[key]
                live_core = (
                    live["calendar_id"],
                    live["action"],
                    live["at_start"],
                    live["events_offset"],
                    live["params"],
                )
                desired_core = (
                    payload["calendar_id"],
                    payload["action"],
                    payload["at_start"],
                    payload["events_offset"],
                    payload["params"],
                )
                if live_core != desired_core:
                    operations.append((types.rest.CustomMethodMethod.PUT, key, payload))
            operations = [
                (types.rest.CustomMethodMethod.DELETE, uuid, dict[str, typing.Any]())
                for uuid in sorted(set(current) - edited)
            ] + operations
            return pool.name, operations

        pool_name, operations = await sync_to_async(_plan, thread_sensitive=True)()

        proxy = RestProxy()
        parent = RestTarget(ServicesPools, "services_pools")
        for method, row_uuid, params in operations:
            await proxy.execute(
                RestTarget(
                    ActionsCalendars,
                    "services_pools/{uuid}/actions",
                    method,
                    args=(row_uuid,) if row_uuid is not None else (),
                    parent=parent,
                ),
                request,
                dict(params),
                parent_uuid=action.target_uuid,
            )

        created = sum(1 for method, _, _ in operations if method == types.rest.CustomMethodMethod.POST)
        modified = sum(1 for method, _, _ in operations if method == types.rest.CustomMethodMethod.PUT)
        removed = sum(1 for method, _, _ in operations if method == types.rest.CustomMethodMethod.DELETE)
        return f'Service pool "{pool_name}" scheduled actions: {created} created, {modified} modified, {removed} removed'


def _validate_uuid(value: typing.Any, where: str, what: str) -> tuple[str | None, str | None]:
    """Validate one uuid string; returns (error, normalized uuid or None)."""
    if not isinstance(value, str):
        return (f"{where} must be the uuid string of {what}", None)
    try:
        normalized = process_uuid(value)
    except ValueError:
        return (f"{where} is not a valid uuid: {value!r}", None)
    return (None, normalized)


def _validate_calendar_id(value: typing.Any, where: str) -> tuple[str | None, str | None]:
    """Validate one row calendar_id; returns (error, normalized uuid or None)."""
    if not isinstance(value, str):
        return (f"{where}.calendar_id must be the uuid string of an existing calendar", None)
    try:
        calendar_id = process_uuid(value)
    except ValueError:
        return (f"{where}.calendar_id is not a valid uuid: {value!r}", None)
    if not models.Calendar.objects.filter(uuid=calendar_id).exists():
        return (f"{where}.calendar_id does not match any existing calendar: {calendar_id}", None)
    return (None, calendar_id)


def _validate_params(action_id: str, params: typing.Any, where: str) -> list[str]:
    """Validate the params object of one row against the action's schema."""
    spec = _action_param_specs(action_id)
    if not isinstance(params, dict):
        return [f"{where}.params must be an object ({_params_hint(spec)})"]
    params_dict = typing.cast("dict[str, typing.Any]", params)
    errors: list[str] = []
    unknown = sorted(set(params_dict) - set(spec))
    missing = sorted(set(spec) - set(params_dict))
    if unknown:
        errors.append(
            f"{where}.params has unknown keys: {', '.join(unknown)} (accepted: {', '.join(sorted(spec)) or 'none'})"
        )
    if missing:
        errors.append(f"{where}.params is missing keys: {', '.join(missing)}")
    for name, param_spec in spec.items():
        if name not in params_dict:
            continue
        errors.extend(_validate_param_value(param_spec, params_dict[name], f"{where}.params.{name}"))
    return errors


def _params_shape(spec: dict[str, JsonObject]) -> str:
    """Tooltip shape of an action's params: ``name: type (default ...)``."""
    if not spec:
        return "no params (use {})"
    return ", ".join(
        f"{name}: {param['type']}"
        + (f" (default {param['default']!r})" if param.get("default") not in ("", None) else "")
        for name, param in sorted(spec.items())
    )


def _params_hint(spec: dict[str, JsonObject]) -> str:
    if not spec:
        return "an empty object when the action has no parameters"
    return "keys: " + ", ".join(f"{name} ({param['type']})" for name, param in spec.items())


def _validate_param_value(param_spec: JsonObject, value: typing.Any, where: str) -> list[str]:
    kind = param_spec["type"]
    if kind == "numeric":
        if isinstance(value, bool) or not isinstance(value, int):
            return [f"{where} must be an integer"]
    elif kind == "bool":
        if not isinstance(value, bool):
            return [f"{where} must be a boolean"]
    elif kind == "transport":
        error, transport_id = _validate_uuid(value, where, "an existing transport")
        if error:
            return [error]
        if not models.Transport.objects.filter(uuid=transport_id).exists():
            return [f"{where} does not match any existing transport: {transport_id}"]
    elif kind == "group":
        return _validate_group_param(value, where)
    return []


def _validate_group_param(value: typing.Any, where: str) -> list[str]:
    """A group param is ``authenticator_uuid@group_uuid`` (the REST shape)."""
    if not isinstance(value, str) or "@" not in value:
        return [f"{where} must be 'authenticator_uuid@group_uuid'"]
    auth_part, _, group_part = value.partition("@")
    auth_error, auth_id = _validate_uuid(
        auth_part, f"{where} (authenticator part)", "an existing authenticator"
    )
    if auth_error:
        return [auth_error]
    group_error, group_id = _validate_uuid(group_part, f"{where} (group part)", "an existing group")
    if group_error:
        return [group_error]
    authenticator = models.Authenticator.objects.filter(uuid=auth_id).first()
    if authenticator is None:
        return [f"{where} authenticator does not exist: {auth_id}"]
    if not authenticator.groups.filter(uuid=group_id).exists():
        return [f"{where} group {group_id} does not belong to authenticator {auth_id}"]
    return []
