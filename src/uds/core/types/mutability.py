"""Mutability overlay metadata for gui elements.

The administration gui (``uds.core.types.ui``) describes a field as a
*widget*: how it renders and what its value means for the form. How the
same field is treated by the *mutability* layer (whether an agent may
propose it, whether it only travels as payload context, whether it never
reaches the agent surface at all) is an orthogonal concern,
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
    """

    role: FieldMutabilityRole = FieldMutabilityRole.PROPOSABLE
    agent_tooltip: str | None = None

    @classmethod
    def proposable(cls, agent_tooltip: str | None = None) -> "FieldMutability":
        return cls(role=FieldMutabilityRole.PROPOSABLE, agent_tooltip=agent_tooltip)

    @classmethod
    def context(cls, agent_tooltip: str | None = None) -> "FieldMutability":
        return cls(role=FieldMutabilityRole.CONTEXT, agent_tooltip=agent_tooltip)

    @classmethod
    def hidden(cls) -> "FieldMutability":
        return cls(role=FieldMutabilityRole.HIDDEN)

    @typing.override
    def as_dict(self) -> dict[str, typing.Any]:
        """Returns a dict with all fields that are not None"""
        return {k: v for k, v in dataclasses.asdict(self).items() if v is not None}
