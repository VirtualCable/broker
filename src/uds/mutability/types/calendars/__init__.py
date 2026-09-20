"""``calendar.update`` / ``.create`` / ``.delete``: named date/time groups
used by pools and rules.

Replicates the REST ``PUT``/``POST``/``DELETE`` on ``/calendars``:
``name``, ``comments`` and ``tags``.

The DELETE removes the calendar along with everything hanging from it:
its rules and the accesses and scheduled actions configured on pools with
it are CASCADE references, so they disappear with it, exactly what the
administration interface does (the pools themselves survive; they simply
lose those time policies).

The rules of a calendar live in the sibling ``rule`` module (its own
action type, ``calendar_rule.create``/``.update``/``.delete``); the import
at the bottom of this module pulls it in so the registry discovers every
family of the package.

Validation, serialization and execution reuse the very same handler
machinery.
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
from ... import verbs as mutability_verbs
from ...base import ActionOperation
from ...etag import item_etag

from . import rule as rule

JsonObject = dict[str, typing.Any]


class CalendarUpdate(mutability_base.MutableActionType):
    """Proposal: create, update, or delete a calendar."""

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
    noun = "Calendar"
    # A calendar is not a module: no subtype gallery, no data_type
    create_has_gallery = False

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
        # Deletion needs no fields: the target itself is what disappears.
        if self.operation is ActionOperation.DELETE:
            return []
        if self.operation is ActionOperation.CREATE:
            return self.create_field_definitions(for_type, target)
        # The agent view derives from the handler gui, so any change
        # there propagates automatically
        return gui_view.agent_definitions(
            self._gui_elements(for_type),
            from_instance=lambda name: name not in self._save_columns(),
        )

    @typing.override
    def create_field_definitions(
        self,
        for_type: str,
        target: db_models.Model | None = None,
    ) -> list[JsonObject]:
        # The creation view of the same gui (no readonly/context fields
        # to unlock here)
        return gui_view.agent_definitions(self._gui_elements(for_type), for_creation=True)

    @typing.override
    def create_validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        # Calendars have no subtype gallery: the data_type is not
        # required. The only thing the base validation cannot enforce is
        # that the name actually carries text.
        errors = super().create_validate_values(for_type or "calendar", values, target)
        if not str(values.get("name", "")).strip():
            errors.append("field name: required")
        return errors

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
        # Operation-independent: a delete-bound instance still
        # fingerprints the full item, while its proposal field surface is
        # empty
        return gui_view.fingerprint_names(self._gui_elements(for_type or "calendar"))

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        fields = self.etag_fields("calendar")
        item_dict: JsonObject = self.snapshot_values(target, fields)
        return item_etag(item_dict, fields)

    # ---------------------------------------------------------- helpers

    @staticmethod
    def _save_columns() -> frozenset[str]:
        return frozenset(name for name, _modifier in Calendars.parse_save_fields(Calendars.FIELDS_TO_SAVE))

    def _gui_elements(self, for_type: str) -> list[types.ui.GuiElement]:
        """The handler gui, ordered as the builder declared.

        ``Calendars.get_gui`` is request-independent; binding it to an
        empty namespace avoids instantiating the full handler (whose
        ``__init__`` resolves authentication).
        """
        shim = typing.cast(typing.Any, _Namespace())
        return sorted(Calendars.get_gui(shim, for_type), key=lambda element: element.gui.order)

    # ---------------------------------------------------- standard texts

    @typing.override
    def tool_title(self) -> str:
        if self.operation is ActionOperation.CREATE:
            return mutability_verbs.create_tool_title("calendar")
        if self.operation is ActionOperation.DELETE:
            return mutability_verbs.delete_tool_title("calendar")
        return self.title

    @typing.override
    def tool_description(self) -> str:
        if self.operation is ActionOperation.CREATE:
            return mutability_verbs.create_tool_description(
                "calendar",
                "name (required), comments and tags; the rules are added afterwards with calendar_rule.create",
            )
        if self.operation is ActionOperation.DELETE:
            return mutability_verbs.delete_tool_description(
                "calendar",
                "the REST removes it along with its rules and the accesses and "
                "scheduled actions configured on pools with it; the pools themselves "
                "survive, they simply lose those time policies — exactly like the "
                "administration interface.",
            )
        return self.description

    # ---------------------------------------------------------- execution

    @typing.override
    async def op_create(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        values = dict(action.values)
        # The POST is form-shaped: every key of FIELDS_TO_SAVE rides,
        # omitted ones take the value the admin form sends on "new".
        params: JsonObject = {
            "name": values.get("name"),
            "comments": values.get("comments", ""),
            "tags": values.get("tags", []),
        }
        new_uuid = await mutability_verbs.execute_create(
            RestTarget(Calendars, "calendars", types.rest.CustomMethodMethod.POST),
            request,
            params,
        )
        return mutability_verbs.created_message("Calendar", str(params["name"]), new_uuid)

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

    @typing.override
    async def op_delete(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The REST DELETE removes the row (and its cascade rules and
        # accesses/actions) synchronously. ALL the ORM work stays out of
        # the async context.
        calendar = typing.cast(
            models.Calendar,
            await sync_to_async(self.resolve_target, thread_sensitive=True)(action.target_uuid),
        )
        await mutability_verbs.execute_delete(
            RestTarget(
                Calendars,
                "calendars",
                types.rest.CustomMethodMethod.DELETE,
                args=(action.target_uuid,),
            ),
            request,
        )
        return f'Calendar "{calendar.name}" deleted'
