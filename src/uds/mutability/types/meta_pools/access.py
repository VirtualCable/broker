"""``metapool.access``: set the access calendar rules of a meta pool.

Relation action type over ``CalendarAccessMeta`` rows (option A:
``calendars`` describes the FINAL set, never a delta). Execution diffs
it against the live rows keyed by ``calendar_id`` and applies the
minimal create/edit/delete sequence through the canonical
``meta_pools/{uuid}/access`` detail surface (the same shared
``AccessCalendars`` handler the service pool surface uses).

Uniqueness of the natural key is enforced in layers: the payload may not
repeat a calendar, and the REST save path rejects (create or edit) a
calendar that already has a rule on the meta pool. There is no DB unique
constraint yet, so live duplicated rows (legacy data) are detected and
refused both when proposing and when planning execution: the diff
cannot promise a deterministic result over them.
"""

import collections.abc
import hashlib
import json
import typing

from asgiref.sync import sync_to_async
from django.db import models as db_models

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.core.util.model import process_uuid
from uds.mcp.rest_proxy import RestProxy, RestTarget
from uds.REST.methods.meta_pools import MetaPools
from uds.REST.methods.op_calendars import AccessCalendars

from ... import base as mutability_base

JsonObject = dict[str, typing.Any]

_ACCESS_ROW_KEYS: typing.Final[frozenset[str]] = frozenset({"calendar_id", "access", "priority"})

_ALLOWED_ACCESS_VALUES: typing.Final[frozenset[str]] = frozenset(
    {types.states.State.ALLOW, types.states.State.DENY}
)

AccessRow = dict[str, typing.Any]
AccessOperation = tuple[types.rest.CustomMethodMethod, str | None, AccessRow]


def _access_rows(meta_pool: models.MetaPool) -> list[AccessRow]:
    """Live access rules as canonical rows (sorted by calendar uuid).

    The rule row uuid is intentionally not part of the projection: the
    calendar is the natural key of the rule (one rule per calendar), the
    row id is an execution detail resolved again at approve time.
    """
    return [
        {"calendar_id": rule.calendar.uuid, "access": rule.access, "priority": rule.priority}
        for rule in meta_pool.calendarAccess.select_related("calendar").order_by("calendar__uuid")
    ]


class MetaPoolAccess(mutability_base.MutableActionType):
    """Proposal: set the complete desired access calendar rules of a meta pool."""

    type_id = "metapool.access"

    title = "Propose meta pool access calendars"
    description = (
        "Propose the complete desired set of access calendar rules of a meta pool "
        "(when the meta pool may be used). The 'calendars' field is the FINAL set, "
        "not a delta: rules missing from it will be removed, new ones added, and "
        "access/priority adjusted. Each rule is one calendar with access ALLOW or "
        "DENY and a priority (lower wins). An empty list removes every rule (the "
        "meta pool then depends only on its fallback access). The proposal does "
        "NOT apply anything: it is queued until an administrator approves it."
    )
    handler = MetaPools
    model = models.MetaPool
    noun = "Meta pool"
    # Discovery without the meta pool uuid is useless here: the agent must
    # see the current rules before it can propose the final set.
    target_scoped_fields = True

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.MetaPool.objects.get(uuid__iexact=target_uuid)
        except models.MetaPool.DoesNotExist:
            raise rest_exceptions.NotFound("Meta pool not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return "metapool"

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        calendars: list[JsonObject] = [
            {"value": str(uuid), "label": name}
            for uuid, name in models.Calendar.objects.order_by("name").values_list("uuid", "name")
        ]
        return [
            {
                "name": "calendars",
                "type": types.ui.FieldType.EDITABLELIST.value,
                "label": "Access calendars",
                "tooltip": (
                    "Complete desired set of access calendar rules. Each item is an object with "
                    "calendar_id (uuid of an existing calendar, choices), access (ALLOW or DENY) "
                    "and priority (integer, lower wins). Rules not listed are removed on "
                    "approval; a calendar can appear only once."
                ),
                "secret": False,
                "row": {
                    "calendar_id": "uuid of an existing calendar (choices)",
                    "access": "ALLOW or DENY",
                    "priority": "integer",
                },
                "choices": calendars,
            }
        ]

    @typing.override
    def validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        errors = super().validate_values(for_type, values, target)
        flat = self.flatten_values(values)
        if "calendars" not in flat:
            errors.append("field calendars is required (the complete desired set; use [] to remove all)")
            return errors
        errors.extend(self._validate_rows(flat["calendars"]))
        if isinstance(target, models.MetaPool):
            live = [row["calendar_id"] for row in _access_rows(target)]
            if len(set(live)) != len(live):
                errors.append(
                    "the meta pool currently has duplicated access rules for the same calendar "
                    "in the database; fix them through the admin UI before proposing again"
                )
        return errors

    @staticmethod
    def _validate_rows(rows: typing.Any) -> list[str]:
        if not isinstance(rows, (list, tuple)):
            return []  # base already flagged "must be a list"
        errors: list[str] = []
        seen: set[str] = set()
        rows_seq = typing.cast("collections.abc.Sequence[typing.Any]", rows)
        for index, row in enumerate(rows_seq):
            where = f"calendars[{index}]"
            if not isinstance(row, dict):
                errors.append(f"{where} must be an object with calendar_id, access and priority")
                continue
            row_dict = typing.cast("dict[str, typing.Any]", row)
            unknown = sorted(set(row_dict) - _ACCESS_ROW_KEYS)
            missing = sorted(_ACCESS_ROW_KEYS - set(row_dict))
            if unknown:
                errors.append(
                    f"{where} has unknown keys: {', '.join(unknown)} (accepted: access, calendar_id, priority)"
                )
            if missing:
                errors.append(f"{where} is missing keys: {', '.join(missing)}")
            calendar_error, calendar_id = _validate_calendar_id(row_dict.get("calendar_id"), where)
            if calendar_error:
                errors.append(calendar_error)
            elif typing.cast("str", calendar_id) in seen:
                errors.append(f"{where} duplicates calendar_id {calendar_id} (a calendar can appear only once)")
            else:
                seen.add(typing.cast("str", calendar_id))
            access = row_dict.get("access")
            if not isinstance(access, str) or access not in _ALLOWED_ACCESS_VALUES:
                errors.append(f"{where}.access must be ALLOW or DENY")
            priority = row_dict.get("priority")
            if isinstance(priority, bool) or not isinstance(priority, int):
                errors.append(f"{where}.priority must be an integer")
        return errors

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        return ["calendars"]

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        meta_pool = typing.cast(models.MetaPool, target)
        wanted = set(names)
        return {"calendars": _access_rows(meta_pool)} if "calendars" in wanted else {}

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        rows = _access_rows(typing.cast(models.MetaPool, target))
        return hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()

    @typing.override
    def target_display_name(self, target: db_models.Model) -> str:
        return typing.cast(models.MetaPool, target).name

    # ------------------------------------------------------- tool text / read

    @typing.override
    def read_title(self) -> str:
        return "Read meta pool access calendars"

    @typing.override
    def read_description(self) -> str:
        return (
            "Read the current access calendar rules of one meta pool: every rule with its "
            "calendar uuid (to use in a metapool.access.set proposal), name, access "
            "(ALLOW/DENY) and priority. Applies immediately; it is a plain read, never a "
            "proposal."
        )

    @typing.override
    def read(self, target_uuid: str) -> JsonObject:
        result = super().read(target_uuid)
        meta_pool = typing.cast(models.MetaPool, self.resolve_target(target_uuid))
        names = {str(uuid): name for uuid, name in models.Calendar.objects.values_list("uuid", "name")}
        result["current_rules"] = [
            {**row, "calendar_name": names.get(str(row["calendar_id"]), "")} for row in _access_rows(meta_pool)
        ]
        return result

    # ---------------------------------------------------------- execution

    @typing.override
    async def op_set(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # Plan the diff in one sync boundary (target + live rows), then run
        # the REST operations. The whole-set CAS check (stale policy DENY)
        # already validated the live set against the approved base before
        # the executor got here.
        def _plan() -> tuple[str, list[AccessOperation]]:
            meta_pool = typing.cast(models.MetaPool, self.resolve_target(action.target_uuid))
            rows = _access_rows(meta_pool)
            current = {row["calendar_id"]: row for row in rows}
            if len(current) != len(rows):
                raise rest_exceptions.RequestError(
                    f'Meta pool "{meta_pool.name}" has duplicated access rules for the same calendar; '
                    "fix them through the admin UI before proposing again"
                )
            # Rule row uuids keyed by calendar uuid (the row id itself is an
            # execution detail, resolved fresh at approve time, never stored)
            rule_uuids = {
                rule.calendar.uuid: rule.uuid for rule in meta_pool.calendarAccess.select_related("calendar")
            }
            desired: dict[str, AccessRow] = {
                process_uuid(typing.cast(str, row["calendar_id"])): {
                    "calendar_id": process_uuid(typing.cast(str, row["calendar_id"])),
                    "access": typing.cast(str, row["access"]),
                    "priority": typing.cast(int, row["priority"]),
                }
                for row in typing.cast("list[AccessRow]", action.values["calendars"])
            }
            operations: list[AccessOperation] = [
                (types.rest.CustomMethodMethod.DELETE, rule_uuids[calendar_id], {})
                for calendar_id in sorted(set(current) - set(desired))
            ]
            operations += [
                (types.rest.CustomMethodMethod.POST, None, desired[calendar_id])
                for calendar_id in sorted(set(desired) - set(current))
            ]
            # calendar_id is equal by construction, so the row dicts compare
            # on access/priority only
            operations += [
                (types.rest.CustomMethodMethod.PUT, rule_uuids[calendar_id], desired[calendar_id])
                for calendar_id in sorted(set(desired) & set(current))
                if current[calendar_id] != desired[calendar_id]
            ]
            return meta_pool.name, operations

        meta_pool_name, operations = await sync_to_async(_plan, thread_sensitive=True)()

        proxy = RestProxy()
        parent = RestTarget(MetaPools, "meta_pools")
        for method, rule_uuid, params in operations:
            await proxy.execute(
                RestTarget(
                    AccessCalendars,
                    "meta_pools/{uuid}/access",
                    method,
                    args=(rule_uuid,) if rule_uuid is not None else (),
                    parent=parent,
                ),
                request,
                dict(params),
                parent_uuid=action.target_uuid,
            )

        created = sum(1 for method, _, _ in operations if method == types.rest.CustomMethodMethod.POST)
        modified = sum(1 for method, _, _ in operations if method == types.rest.CustomMethodMethod.PUT)
        removed = sum(1 for method, _, _ in operations if method == types.rest.CustomMethodMethod.DELETE)
        return f'Meta pool "{meta_pool_name}" access calendars: {created} created, {modified} modified, {removed} removed'


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
