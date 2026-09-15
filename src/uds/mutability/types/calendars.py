"""``calendar.update`` and ``calendar_rule.update``: propose modifications
to calendars and their rules.

``calendar.update`` replicates the REST ``PUT /calendars/{uuid}``
(``name``, ``comments``, ``tags``).

``calendar_rule.update`` replicates ``PUT /calendars/{uuid}/rules/{uuid}``
(a detail: permissions are inherited from the calendar, exactly like
services). The rules detail has no gui definition, so the mutable fields
are the ones its ``save_item`` accepts, with the same shapes: ``start``
and ``end`` are UNIX timestamps (seconds), ``frequency``/``duration_unit``
are the enum names of the model.

Validation, serialization and execution reuse the very same handler
machinery.
"""

import collections.abc
import datetime
import typing
from types import SimpleNamespace as _Namespace

from asgiref.sync import sync_to_async
from django.db import models as db_models
from django.utils import timezone

from uds import models
from uds.core import types
from uds.REST.methods.calendars import Calendars
from uds.REST.methods.calendarrules import CalendarRules
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.mcp.rest_proxy import RestProxy, RestTarget
from uds.models.calendar_rule import DurationInfo, FrequencyInfo

from .. import base as mutability_base
from .. import gui_view
from ..etag import item_etag

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


def _def_from_choice(name: str, label: str, tooltip: str, choices: tuple[tuple[str, str], ...]) -> JsonObject:
    return {
        "name": name,
        "type": "choice",
        "label": label,
        "tooltip": tooltip,
        "secret": False,
        "choices": [value for value, _title in choices],
    }


class CalendarUpdate(mutability_base.MutableActionType):
    """Proposal: update an existing calendar."""

    type_id = "calendar"
    title = "Propose calendar update"
    description = (
        "Propose changes to an existing calendar (named date/time groups used by "
        "pools and rules). The proposal does NOT apply anything: it is queued until "
        "an administrator approves it. Mutable fields are name, comments and tags; "
        "use get_mutable_fields to discover them and their current values. The rules "
        "of the calendar are proposed with calendar_rule.update."
    )
    handler = Calendars

    # ------------------------------------------------------------- hooks

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.Calendar.objects.get(uuid__iexact=target_uuid)
        except models.Calendar.DoesNotExist:
            raise rest_exceptions.NotFound("Calendar not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return "calendar"

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        shim = typing.cast(typing.Any, _Namespace())
        # The gui fields are exactly the handler's own FIELDS_TO_SAVE
        # columns; the agent view derives from the handler gui, so any
        # change there propagates here automatically
        columns = frozenset(name for name, _modifier in Calendars.parse_save_fields(Calendars.FIELDS_TO_SAVE))
        return gui_view.agent_definitions(
            sorted(Calendars.get_gui(shim, for_type), key=lambda element: element.gui.order),
            from_instance=lambda name: name not in columns,
        )

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        calendar = typing.cast(models.Calendar, target)
        wanted = set(names)
        snapshot: JsonObject = {}
        for name in wanted:
            if name == "name":
                snapshot[name] = calendar.name
            elif name == "comments":
                snapshot[name] = calendar.comments
            elif name == "tags":
                snapshot[name] = sorted(t.tag for t in calendar.tags.all())  # type: ignore[attr-defined]
        return snapshot

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        # The merged PUT carries exactly the proposable surface (no context
        # fields needed), so fingerprint == definitions
        return [d["name"] for d in self.field_definitions(for_type)]

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        fields = self.etag_fields("calendar")
        item_dict: JsonObject = self.snapshot_values(target, fields)
        return item_etag(item_dict, fields)

    @typing.override
    async def op_update(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The PUT is form-shaped (every FIELDS_TO_SAVE entry is required):
        # merge the proposal over the CAS-verified current values, so a
        # partial proposal applies instead of failing on approval.
        # ALL the ORM work must stay out of the async context.
        def _build_params() -> tuple[str, JsonObject]:
            calendar = typing.cast(models.Calendar, self.resolve_target(action.target_uuid))
            params = self.snapshot_values(calendar, self.etag_fields("calendar"))
            params.update(action.values)
            return calendar.name, params

        calendar_name, params = await sync_to_async(_build_params, thread_sensitive=True)()
        await RestProxy().execute(
            RestTarget(
                Calendars,
                "calendars",
                types.rest.CustomMethodMethod.PUT,
                args=(action.target_uuid,),
            ),
            request,
            params,
        )
        return f'Calendar "{calendar_name}" updated'


class CalendarRuleUpdate(mutability_base.MutableActionType):
    """Proposal: update an existing rule of a calendar (detail type)."""

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
        defs: list[JsonObject] = [
            {"name": "name", "type": "text", "label": "Name", "tooltip": "Name of the rule", "secret": False},
            {
                "name": "comments",
                "type": "text",
                "label": "Comments",
                "tooltip": "Comments of the rule",
                "secret": False,
            },
            _def_from_choice(
                "frequency", "Frequency", "How often the rule repeats", FrequencyInfo.as_choices()
            ),
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
            _def_from_choice(
                "duration_unit", "Duration unit", "Unit of the duration", DurationInfo.as_choices()
            ),
        ]
        return defs

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
