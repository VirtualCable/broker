"""Agent view of gui definitions (GUI -> MCP coupling).

The administration gui (``uds.core.types.ui.GuiElement``) is the single
source of truth for what a field *is*; the optional ``overlay`` slot can
carry a ``uds.core.types.mutability.FieldMutability`` saying how the
mutability layer treats it (other overlay payloads are ignored here).
This module is the renderer that combines both into the field definitions
the MCP agent surface consumes: label, tooltip, type, choices, constraints
and secret flag come straight from the gui, so any change there propagates
here automatically.

Support is an explicit allowlist (``REPRESENTABLE_FIELD_TYPES``): a gui
field type the view does not know how to express as a JSON definition is
logged as a warning and silently omitted (never proposed), instead of
reaching the agent with a definition the validation layer could not
check. When a new widget type reaches the administration gui and the
mutability surface should offer it, it is added to the allowlist (plus a
json-side check in ``MutableActionType._field_type_errors`` when it is
not a plain string).
"""

import collections.abc
import enum
import logging
import typing

from django.utils.functional import Promise

from uds.core.types import ui as ui_types
from uds.core.types.mutability import FieldMutability, FieldMutabilityRole

logger = logging.getLogger(__name__)

JsonObject = dict[str, typing.Any]

# Gui field types this view can express as agent definitions. Everything
# not listed here triggers the "not supported" path: warning + omission.
REPRESENTABLE_FIELD_TYPES: typing.Final[frozenset[ui_types.FieldType]] = frozenset(
    {
        ui_types.FieldType.TEXT,
        ui_types.FieldType.TEXT_AUTOCOMPLETE,
        ui_types.FieldType.NUMERIC,
        ui_types.FieldType.PASSWORD,
        ui_types.FieldType.HIDDEN,
        ui_types.FieldType.CHOICE,
        ui_types.FieldType.MULTICHOICE,
        ui_types.FieldType.EDITABLELIST,
        ui_types.FieldType.CHECKBOX,
        ui_types.FieldType.IMAGECHOICE,
        ui_types.FieldType.TAGLIST,
    }
)

# Field types whose value must never be disclosed to the agent
SECRET_FIELD_TYPES: typing.Final[frozenset[ui_types.FieldType]] = frozenset(
    {ui_types.FieldType.PASSWORD, ui_types.FieldType.HIDDEN}
)


def mutability_of(element: ui_types.GuiElement) -> FieldMutability | None:
    """The mutability annotation of a gui element, if any.

    ``GuiElement.overlay`` is a generic slot shared by consumer layers, so
    the mutability view validates it: anything that is not a
    ``FieldMutability`` (including no overlay at all) means "no mutability
    annotation" and falls back to the historical default treatment.
    """
    overlay = element.overlay
    return overlay if isinstance(overlay, FieldMutability) else None


def role_of(element: ui_types.GuiElement) -> FieldMutabilityRole:
    """Effective mutability role of a gui element (default: proposable)."""
    annotation = mutability_of(element)
    return annotation.role if annotation else FieldMutabilityRole.PROPOSABLE


def agent_definition(element: ui_types.GuiElement) -> JsonObject | None:
    """Definition of one gui element for the agent surface, or None.

    ``None`` means "not part of the proposable surface": either by role
    (only PROPOSABLE fields are proposed; context references travel in the
    fingerprint / PUT payload but the agent must not touch them, hidden
    fields never apply, and relations travel through their own detail
    surface, so an update-style type silently ignores them), or because
    the widget type is not representable yet (logged as a warning).
    """
    if role_of(element) is not FieldMutabilityRole.PROPOSABLE:
        return None

    gui = element.gui
    field_type = gui.type
    if field_type not in REPRESENTABLE_FIELD_TYPES:
        logger.warning(
            "mutability agent view: field %r has an unrepresentable gui type %r; "
            "it is not proposed to agents (add support in uds.mutability.gui_view if intended)",
            element.name,
            str(field_type),
        )
        return None

    annotation = mutability_of(element)
    definition: JsonObject = {
        "name": element.name,
        "type": field_type.value,
        "label": _text(gui.label),
        "tooltip": _text((annotation.agent_tooltip if annotation else None) or gui.tooltip),
        "secret": field_type in SECRET_FIELD_TYPES,
    }
    if gui.required:
        definition["required"] = True
    if gui.readonly:
        definition["readonly"] = True
    if gui.default is not None:
        default = gui.default() if callable(gui.default) else gui.default
        definition["default"] = _scalar(default)
    if gui.min_value is not None:
        definition["min"] = gui.min_value
    if gui.max_value is not None:
        definition["max"] = gui.max_value
    if gui.length is not None:
        definition["length"] = gui.length
    if gui.pattern != ui_types.FieldPatternType.NONE:
        definition["pattern"] = str(gui.pattern)
    choices = _agent_choices(gui.choices)
    if choices:
        definition["choices"] = choices
    if gui.tab:
        definition["tab"] = _text(gui.tab)
    return definition


def agent_definitions(elements: collections.abc.Iterable[ui_types.GuiElement]) -> list[JsonObject]:
    """Definitions of every proposable, representable element."""
    return [definition for element in elements if (definition := agent_definition(element)) is not None]


def fingerprint_names(elements: collections.abc.Iterable[ui_types.GuiElement]) -> list[str]:
    """Names taking part in the whole-item fingerprint.

    Everything the form-shaped payload carries except the hidden fields:
    proposable fields and context references. Relations are not part of
    the parent PUT payload and belong to their own relation fingerprint.
    """
    return [
        element.name
        for element in elements
        if role_of(element) not in (FieldMutabilityRole.HIDDEN, FieldMutabilityRole.RELATION)
    ]


def _agent_choices(choices: ui_types.ChoicesType | None) -> list[JsonObject] | None:
    """Gui choices as ``{value, label}`` for the agent (labels preserved).

    A callable universe (dynamic/lazy) is not resolved here: discovering
    those is a dedicated tool's concern, so the field carries no inline
    choices in that case.
    """
    if choices is None or callable(choices):
        return None
    resolved = [{"value": _scalar(item.id), "label": _text(item.text)} for item in choices]
    return resolved or None


def _text(value: typing.Any) -> str:
    """Lazy translations and promises as plain text."""
    return str(value) if isinstance(value, (str, Promise)) else value  # pyright: ignore[reportReturnType]


def _scalar(value: typing.Any) -> typing.Any:
    """Enum-valued gui data as its JSON scalar (StrEnum/IntEnum members)."""
    return value.value if isinstance(value, enum.Enum) else value
