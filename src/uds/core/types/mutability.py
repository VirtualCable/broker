"""Mutability overlay metadata for gui elements.

The administration gui (``uds.core.types.ui``) describes a field as a
*widget*: how it renders and what its value means for the form. How the
same field is treated by the *mutability* layer (whether an agent may
propose it, whether it only travels as payload context, whether it is a
relation managed through its own detail surface) is an orthogonal concern,
so it is not part of ``FieldInfo`` (which the REST ``/gui`` payload
serializes verbatim) and not computed here either.

This module only declares that annotation, as a ``GuiOverlay`` attached to
the ``overlay`` slot of ``GuiElement``. It is deliberately pure data: the
view that turns gui + mutability into the agent field surface (and that
decides what is actually supported, reusing the existing gui field
machinery) lives in ``uds.mutability``, the consumer layer.
"""

import dataclasses
import enum
import typing

from . import ui


class FieldMutabilityRole(enum.StrEnum):
    """How the mutability layer treats a gui field."""

    PROPOSABLE = "proposable"
    """Agent may propose a new value (the historical default treatment)."""

    CONTEXT = "context"
    """Not proposable, but travels on every payload (e.g. FK references
    required by form-shaped PUTs) and takes part in the fingerprint."""

    RELATION = "relation"
    """Managed through its own detail surface and relation action type;
    announced here so the gui remains the single registry of mutable
    surface."""

    HIDDEN = "hidden"
    """Never part of the mutable surface (m2m relations of module-backed
    models, for instance)."""


@dataclasses.dataclass
class FieldMutability(ui.GuiOverlay):
    """Optional mutability annotation of a ``GuiElement``.

    It is a ``GuiOverlay``: it plugs into the generic ``overlay`` slot of
    the gui element (annotated via ``uds.core.util.ui.GuiBuilder``) and
    declares how the mutability layer treats the field.

    Every member has a default, so the effective configuration of an
    unannotated element stays exactly the historical one: proposable, with
    the agent tooltip equal to the human one.

    The relation members are declarative metadata only in this phase: the
    generic relation machinery reads them directly, they never influence
    the widget nor the REST GUI payload.
    """

    role: FieldMutabilityRole = FieldMutabilityRole.PROPOSABLE
    agent_tooltip: str | None = None

    # --- relation fields (role == RELATION); ignored otherwise ---------

    item_type: str | None = None
    item_label: str | None = None
    # Dotted path of the model providing the assignable universe
    # (e.g. "uds.models.ServicePool"); None when the universe is not a
    # finite local set and must be discovered through dedicated tools
    # (e.g. authenticator groups).
    choices_model: str | None = None
    # Dotted import path of the detail handler managing the relation
    detail_handler: str | None = None
    detail_path: str | None = None
    parent_collection: str | None = None
    relation_manager: str | None = None
    row_schema: typing.Any | None = None

    @classmethod
    def proposable(cls, agent_tooltip: str | None = None) -> "FieldMutability":
        return cls(role=FieldMutabilityRole.PROPOSABLE, agent_tooltip=agent_tooltip)

    @classmethod
    def context(cls, agent_tooltip: str | None = None) -> "FieldMutability":
        return cls(role=FieldMutabilityRole.CONTEXT, agent_tooltip=agent_tooltip)

    @classmethod
    def hidden(cls) -> "FieldMutability":
        return cls(role=FieldMutabilityRole.HIDDEN)

    @classmethod
    def relation(
        cls,
        *,
        item_type: str,
        item_label: str,
        detail_handler: str,
        detail_path: str,
        parent_collection: str,
        relation_manager: str,
        choices_model: str | None = None,
        agent_tooltip: str | None = None,
        row_schema: typing.Any | None = None,
    ) -> "FieldMutability":
        return cls(
            role=FieldMutabilityRole.RELATION,
            agent_tooltip=agent_tooltip,
            item_type=item_type,
            item_label=item_label,
            choices_model=choices_model,
            detail_handler=detail_handler,
            detail_path=detail_path,
            parent_collection=parent_collection,
            relation_manager=relation_manager,
            row_schema=row_schema,
        )

    @typing.override
    def as_dict(self) -> dict[str, typing.Any]:
        """Returns a dict with all fields that are not None"""
        return {k: v for k, v in dataclasses.asdict(self).items() if v is not None}
