"""GUI callback resolver for agents.

Module-based resources (services, os managers, transports, ...)
can declare gui fields whose choices depend on other field values: the
field carries ``fills`` metadata (a named callback plus the fields whose
values it needs) and the administration frontend resolves the choices on
demand through the ``gui/{callback_name}`` surface. Agents use the tool
in this module for the same purpose: it executes the callback through
the canonical REST handler (so the staff-role gate and the callback
implementation stay the only ones), and the response is self-describing —
each entry names the field it fills with its available choices, so the
agent learns which field to update from the answer itself, without any
static knowledge of the filler targets.
"""

import typing

from uds.core import types
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.REST.methods.gui_callback import Callback

from ..catalog import ToolDefinition
from ..rest_proxy import RestProxy, RestTarget
from .helpers import GET, check_required, schema, string_property

JsonObject = dict[str, typing.Any]


def _as_json(result: typing.Any) -> typing.Any:
    """Callback results as plain json (ChoiceItem instances as dicts)."""
    if not isinstance(result, list):
        return result
    normalized: list[typing.Any] = []
    for raw_entry in typing.cast("list[typing.Any]", result):
        if not isinstance(raw_entry, dict):
            normalized.append(raw_entry)
            continue
        entry = typing.cast("dict[str, typing.Any]", raw_entry)
        choices = entry.get("choices")
        if isinstance(choices, list):
            entry["choices"] = [
                choice.as_dict() if isinstance(choice, types.ui.ChoiceItem) else choice
                for choice in typing.cast("list[typing.Any]", choices)
            ]
        normalized.append(entry)
    return normalized


async def _executor(arguments: JsonObject, request: ExtendedHttpRequestWithUser | None = None) -> typing.Any:
    check_required(arguments, ("callback_name",))
    raw = arguments.get("parameters")
    params: JsonObject = dict(typing.cast("dict[str, typing.Any]", raw)) if isinstance(raw, dict) else {}
    return _as_json(
        await RestProxy().execute(
            RestTarget(Callback, "gui", GET, args=(str(arguments["callback_name"]),)),
            request,
            params,
        )
    )


def curated_tools() -> tuple[ToolDefinition, ...]:
    """The gui callback resolution tool."""
    return (
        ToolDefinition(
            name="get_gui_field_choices",
            title="Get gui field choices",
            description=(
                "Resolve the choices of a gui field whose values are dynamic (fields "
                "whose definition carries a 'choices_callback' annotation). Pass the "
                "callback name and the current values of the fields it depends on (the "
                "annotation lists them; use the stored values of the item when editing "
                "it, or the values already chosen for the proposal). The response is a "
                "list of entries naming the field each one fills with its available "
                "choices: use the entry whose name matches the field you are proposing. "
                "An unknown callback answers not found."
            ),
            input_schema=schema(
                {
                    "callback_name": string_property(
                        "Name of the gui callback (the 'name' of the field's choices_callback annotation)."
                    ),
                    "parameters": {
                        "type": "object",
                        "description": "Current values of the fields the callback depends on (field name -> value), as listed in the annotation.",
                        "additionalProperties": True,
                    },
                },
                required=("callback_name",),
            ),
            access="Available to staff users (the backing gui callback surface requires staff role).",
            returns="List of fields filled by the callback, each with its available choices.",
            executor=_executor,
        ),
    )
