"""``calendar.update``: propose modifications to an existing calendar.

``calendar.update`` replicates the REST ``PUT /calendars/{uuid}``
(``name``, ``comments``, ``tags``).

Validation, serialization and execution reuse the very same handler
machinery.

The rules of a calendar live in the sibling ``rule`` module (its own
action type, ``calendar_rule.update``); the import at the bottom of this
module pulls it in so the registry discovers every family of the
package.
"""

import collections.abc
import typing
from types import SimpleNamespace as _Namespace

from asgiref.sync import sync_to_async
from django.db import models as db_models

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.mcp.rest_proxy import RestProxy, RestTarget
from uds.REST.methods.calendars import Calendars

from ... import base as mutability_base
from ... import gui_view
from ...etag import item_etag

from . import rule as rule

JsonObject = dict[str, typing.Any]


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
