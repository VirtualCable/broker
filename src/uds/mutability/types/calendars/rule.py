"""``calendar_rule.create`` / ``.update`` / ``.delete``: the rules of a calendar.

Replicates ``POST``/``PUT``/``DELETE /calendars/{uuid}/rules[/{uuid}]`` (a
detail: permissions are inherited from the calendar, exactly like
services). The rules detail has no gui definition, so the mutable fields
are the ones its ``save_item`` accepts, with the same shapes: ``start``
and ``end`` are UNIX timestamps (seconds), ``frequency``/``duration_unit``
are the enum names of the model.

A creation is a detail of its calendar: the proposal targets the CALENDAR
uuid (``create_needs_parent``), and MANAGEMENT over that calendar is the
create permission. The rules handler's create branch does not report the
new uuid back (a REST quirk, not something this family changes), so the
creation message names the rule without a uuid.

The DELETE removes just the rule; the calendar survives (its own
``modified`` stamp is touched, exactly what the handler does), and nothing
else references a single rule.

Validation, serialization and execution reuse the very same handler
machinery.
"""

import collections.abc
import datetime
import typing

from asgiref.sync import sync_to_async
from django.db import models as db_models
from django.utils import timezone

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.mcp.rest_proxy import RestProxy, RestTarget
from uds.models.calendar_rule import DurationInfo, FrequencyInfo
from uds.REST.methods.calendars import Calendars
from uds.REST.methods.calendarrules import CalendarRules

from ... import base as mutability_base
from ... import verbs as mutability_verbs
from ...base import ActionOperation
from ...etag import item_etag

JsonObject = dict[str, typing.Any]

# Fields the rules detail handler accepts on its PUT (same names/order)
_RULE_FIELDS: typing.Final[tuple[str, ...]] = (
    "name",
    "comments",
    "frequency",
    "start",
    "end",
    "interval",
    "duration",
    "duration_unit",
)

# Timestamp fields that are numeric but accept the null sentinel (end, and
# start in a create payload that omits nothing): the handler reads them
# straight from the params and a None end means "no end", exactly what the
# admin client sends. The generic numeric check would reject it, so these
# names are excused from it in this family.
_NULLABLE_NUMERIC: typing.Final[frozenset[str]] = frozenset({"end"})


def _def_from_choice(name: str, label: str, tooltip: str, choices: tuple[tuple[str, str], ...]) -> JsonObject:
    return {
        "name": name,
        "type": "choice",
        "label": label,
        "tooltip": tooltip,
        "secret": False,
        "choices": [value for value, _title in choices],
    }


def _rule_definitions() -> list[JsonObject]:
    """The whole field surface the rules handler ``save_item`` accepts.

    Shared by update and create: the rules detail builds the same form for
    a new rule and for editing one (its handler never distinguishes them),
    so the two verbs propose exactly the same fields.
    """
    return [
        {"name": "name", "type": "text", "label": "Name", "tooltip": "Name of the rule", "secret": False},
        {
            "name": "comments",
            "type": "text",
            "label": "Comments",
            "tooltip": "Comments of the rule",
            "secret": False,
        },
        _def_from_choice("frequency", "Frequency", "How often the rule repeats", FrequencyInfo.as_choices()),
        {
            "name": "start",
            "type": "numeric",
            "label": "Start",
            "tooltip": "Start of the rule (UNIX timestamp, seconds)",
            "secret": False,
        },
        {
            "name": "end",
            "type": "numeric",
            "label": "End",
            "tooltip": "End of the rule (UNIX timestamp, seconds; null means no end)",
            "secret": False,
        },
        {
            "name": "interval",
            "type": "numeric",
            "label": "Repeat every",
            "tooltip": "Repeat interval (must be greater than zero; for WEEKDAYS each bit is a day, bit 0 = Sunday)",
            "secret": False,
            "min": 1,
        },
        {
            "name": "duration",
            "type": "numeric",
            "label": "Duration",
            "tooltip": "Duration of the rule validity (in duration_unit units)",
            "secret": False,
        },
        _def_from_choice("duration_unit", "Duration unit", "Unit of the duration", DurationInfo.as_choices()),
    ]


class CalendarRuleUpdate(mutability_base.MutableActionType):
    """Proposal: create, update, or delete a rule of a calendar (detail type)."""

    type_id = "calendar_rule"
    title = "Propose calendar rule update"
    description = (
        "Propose changes to an existing rule of a calendar. The proposal does NOT apply "
        "anything: it is queued until an administrator approves it. Mutable fields are "
        "name, comments, frequency, start and end (UNIX timestamps in seconds), "
        "interval, duration and duration_unit; use get_mutable_fields with the rule "
        "uuid to discover them and their current values."
    )
    handler = CalendarRules
    # A rule is created inside a calendar: the proposal targets the
    # calendar uuid, and it is not a module (no subtype gallery).
    create_needs_parent = True
    create_has_gallery = False

    # ------------------------------------------------- generic CAS layer

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.CalendarRule.objects.get(uuid__iexact=target_uuid)
        except models.CalendarRule.DoesNotExist:
            raise rest_exceptions.NotFound("Calendar rule not found") from None

    @typing.override
    def permission_target(self, target: db_models.Model) -> db_models.Model:
        # A rule is a detail of its calendar: MANAGEMENT is held over the
        # parent, exactly like the REST dispatcher checks it.
        return typing.cast(models.CalendarRule, target).calendar

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return "calendar_rule"

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        # Deletion needs no fields: the target itself is what disappears.
        if self.operation is ActionOperation.DELETE:
            return []
        return _rule_definitions()

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        rule = typing.cast(models.CalendarRule, target)
        wanted = set(names)
        snapshot: JsonObject = {}
        for name in wanted:
            if name == "name":
                snapshot[name] = rule.name
            elif name == "comments":
                snapshot[name] = rule.comments
            elif name == "frequency":
                snapshot[name] = rule.frequency
            elif name == "start":
                snapshot[name] = int(rule.start.timestamp())
            elif name == "end":
                # Same shape the REST item serialization uses for end
                snapshot[name] = (
                    int(timezone.make_aware(datetime.datetime.combine(rule.end, datetime.time.max)).timestamp())
                    if rule.end
                    else None
                )
            elif name == "interval":
                snapshot[name] = rule.interval
            elif name == "duration":
                snapshot[name] = rule.duration
            elif name == "duration_unit":
                snapshot[name] = rule.duration_unit
        return snapshot

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        return list(_RULE_FIELDS)

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        fields = self.etag_fields("calendar_rule")
        item_dict: JsonObject = self.snapshot_values(target, fields)
        return item_etag(item_dict, fields)

    # ---------------------------------------------------------- creation

    @typing.override
    def resolve_create_parent(self, parent_uuid: str) -> db_models.Model:
        # The container of a rule creation is its calendar
        try:
            return models.Calendar.objects.get(uuid__iexact=parent_uuid)
        except models.Calendar.DoesNotExist:
            raise rest_exceptions.NotFound("Calendar not found") from None

    @typing.override
    def create_field_definitions(
        self, for_type: str, target: db_models.Model | None = None
    ) -> list[JsonObject]:
        # Same surface as the update (the rules handler builds one form)
        return _rule_definitions()

    @typing.override
    def create_validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        # The family has no subtype gallery: the data_type is not
        # required. The handler's save_item reads every field straight
        # from the params, and only comments/end/interval/duration/
        # duration_unit have defaults in the payload builder, so name,
        # frequency and start are the ones the proposal must carry.
        errors = super().create_validate_values(for_type or "calendar_rule", values, target)
        for field in ("name", "frequency"):
            if not str(values.get(field, "")).strip():
                errors.append(f"field {field}: required")
        if values.get("start") is None:
            errors.append("field start: required")
        return errors

    @typing.override
    @staticmethod
    def _field_type_errors(name: str, value: typing.Any, definition: JsonObject) -> list[str]:
        # ``end`` accepts the null sentinel ("no end", exactly what the
        # admin client sends on every permanent rule); the generic
        # numeric check does not know field semantics and would reject
        # it, so this family excuses it here and defers the rest.
        if value is None and name in _NULLABLE_NUMERIC:
            return []
        return mutability_base.MutableActionType._field_type_errors(name, value, definition)

    # ---------------------------------------------------- standard texts

    @typing.override
    def tool_title(self) -> str:
        if self.operation is ActionOperation.CREATE:
            return mutability_verbs.create_tool_title("calendar rule")
        if self.operation is ActionOperation.DELETE:
            return mutability_verbs.delete_tool_title("calendar rule")
        return self.title

    @typing.override
    def tool_description(self) -> str:
        if self.operation is ActionOperation.CREATE:
            return mutability_verbs.create_tool_description(
                "calendar rule",
                "name (required), comments, frequency, start (UNIX timestamp in seconds), "
                "end (null for no end), interval, duration and duration_unit",
                needs_parent=True,
            )
        if self.operation is ActionOperation.DELETE:
            return mutability_verbs.delete_tool_description(
                "calendar rule",
                "the REST removes it from its calendar (the calendar survives, its "
                "modified stamp is refreshed) — exactly like the administration "
                "interface.",
            )
        return self.description

    # ------------------------------------------------------------- hooks

    @typing.override
    async def op_update(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # ORM work must stay out of the async context, including the
        # parent lookup (the calendar FK needs a query)
        def _resolve() -> tuple[models.CalendarRule, str]:
            rule = typing.cast(models.CalendarRule, self.resolve_target(action.target_uuid))
            return rule, rule.calendar.uuid

        rule, calendar_uuid = await sync_to_async(_resolve, thread_sensitive=True)()
        # The rules PUT is form-shaped (all fields required): merge the
        # proposal over the CAS-verified current values. Any drift between
        # proposal and approval has already revoked the action, so the
        # merged payload is exactly what the administrator approved.
        params: JsonObject = self.snapshot_values(rule, self.etag_fields("calendar_rule"))
        params.update(action.values)
        target = RestTarget(
            CalendarRules,
            "calendars/{uuid}/rules",
            types.rest.CustomMethodMethod.PUT,
            args=(action.target_uuid,),
            parent=RestTarget(Calendars, "calendars"),
        )
        # Detail targets need the parent uuid to resolve (and permission
        # check) the calendar, so the sync boundary is invoked directly.
        await sync_to_async(RestProxy._execute_sync, thread_sensitive=True)(
            target, request, params, calendar_uuid
        )
        return f'Calendar rule "{rule.name}" updated'

    @typing.override
    async def op_create(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The creation proposal targets the CALENDAR; the POST is
        # form-shaped (save_item reads every field straight from the
        # params), so every key rides, the optional ones falling back to
        # the admin form's "new" values (empty comments, no end, interval
        # 1, zero duration in minutes). ALL the ORM work stays out of the
        # async context.
        def _build_params() -> tuple[str, str, JsonObject]:
            calendar = self.resolve_create_parent(action.target_uuid)
            values = dict(action.values)
            params: JsonObject = {
                "name": values.get("name"),
                "comments": values.get("comments", ""),
                "frequency": values.get("frequency"),
                "start": values.get("start"),
                "end": values.get("end"),
                "interval": values.get("interval", 1),
                "duration": values.get("duration", 0),
                "duration_unit": values.get("duration_unit", "MINUTES"),
            }
            return str(params["name"]), str(typing.cast(models.Calendar, calendar).uuid), params

        name, calendar_uuid, params = await sync_to_async(_build_params, thread_sensitive=True)()
        # The rules create branch does not report the new uuid (a REST
        # quirk); the message names the rule without it.
        new_uuid = await mutability_verbs.execute_create(
            RestTarget(
                CalendarRules,
                "calendars/{uuid}/rules",
                types.rest.CustomMethodMethod.POST,
                parent=RestTarget(Calendars, "calendars"),
            ),
            request,
            params,
            calendar_uuid,
        )
        return mutability_verbs.created_message("Calendar rule", name, new_uuid)

    @typing.override
    async def op_delete(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The REST DELETE removes the rule from its calendar and refreshes
        # the calendar's modified stamp. ALL the ORM work stays out of the
        # async context, including the parent lookup (the calendar FK).
        def _resolve() -> tuple[str, str]:
            rule = typing.cast(models.CalendarRule, self.resolve_target(action.target_uuid))
            return rule.name, str(rule.calendar.uuid)

        rule_name, calendar_uuid = await sync_to_async(_resolve, thread_sensitive=True)()
        await mutability_verbs.execute_delete(
            RestTarget(
                CalendarRules,
                "calendars/{uuid}/rules",
                types.rest.CustomMethodMethod.DELETE,
                args=(action.target_uuid,),
                parent=RestTarget(Calendars, "calendars"),
            ),
            request,
            calendar_uuid,
        )
        return f'Calendar rule "{rule_name}" deleted'
