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

Two omissions are policies, not support gaps, and need no annotation nor
warning: ``INFO`` fields (internal display helpers) and ``readonly``
fields (the REST side refuses to change them too). ``from_instance`` is
per action type context (which names are module-instance configuration
instead of model columns), so it is a renderer input, never overlay data.
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


def is_proposable(element: ui_types.GuiElement) -> bool:
    """Whether an element can reach the agent surface at all.

    Role (only PROPOSABLE fields apply), plus the two universal omissions:
    INFO helpers (internal display fields) and readonly fields (the REST
    side refuses to change them too, so proposing one can only fail). The
    representable-type allowlist is NOT considered here: this answers
    "mutable by role/semantics", the renderer decides "expressable as a
    json definition" separately.
    """
    if role_of(element) is not FieldMutabilityRole.PROPOSABLE:
        return False
    return element.gui.type != ui_types.FieldType.INFO and not element.gui.readonly


def proposable_elements(elements: collections.abc.Iterable[ui_types.GuiElement]) -> list[ui_types.GuiElement]:
    """Elements that can reach the agent surface (see ``is_proposable``)."""
    return [element for element in elements if is_proposable(element)]


def fingerprint_names(elements: collections.abc.Iterable[ui_types.GuiElement]) -> list[str]:
    """Names taking part in the whole-item fingerprint.

    Everything the form-shaped payload carries except the non-fingerprint
    fields: hidden and relations never travel in it. INFO and readonly
    fields ARE part of the fingerprint: the CAS base (compare-and-swap:
    the proposal-time snapshot that approval re-checks) must track the
    whole form state the PUT rebuilds from, exactly like the handler's
    own ETag.
    """
    return [
        element.name
        for element in elements
        if role_of(element) not in (FieldMutabilityRole.HIDDEN, FieldMutabilityRole.RELATION)
        and element.gui.type != ui_types.FieldType.INFO
    ]


def agent_definition(
    element: ui_types.GuiElement,
    from_instance: collections.abc.Callable[[str], bool] | None = None,
) -> JsonObject | None:
    """Definition of one gui element for the agent surface, or None.

    ``None`` means "not part of the proposable surface": see
    :func:`is_proposable` for the role/semantics omissions, plus the
    representable-type allowlist (a missing widget type is logged as a
    warning). ``from_instance`` marks each definition with whether the
    name resolves to module-instance configuration instead of a model
    column; it is per action type context, not per field annotation.
    """
    if not is_proposable(element):
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
    if from_instance is not None:
        definition["from_instance"] = bool(from_instance(element.name))
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


def agent_definitions(
    elements: collections.abc.Iterable[ui_types.GuiElement],
    from_instance: collections.abc.Callable[[str], bool] | None = None,
) -> list[JsonObject]:
    """Definitions of every proposable, representable element."""
    return [
        definition
        for element in elements
        if (definition := agent_definition(element, from_instance=from_instance)) is not None
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
