"""Abstract definition of a mutable action type.

A ``MutableActionType`` declares one well-scoped mutation an agent may
*propose* (never apply directly): which REST handler owns it, which
fields are mutable (from the same gui definitions the admin forms use),
and how to execute it through the canonical REST machinery once an
administrator approves it.

The generic CAS machinery (server-side snapshots, stale detection,
descriptions for the agent and fresh diffs for the administrator) lives
here so every concrete type only implements the small domain hooks.
"""

import abc
import collections.abc
import enum
import typing

from django.db import models as db_models

from uds.core import types
from uds.REST.handlers import Handler

JsonObject = dict[str, typing.Any]


class StalePolicy(enum.StrEnum):
    """What to do at approval time when the target drifted from the base.

    ``DENY`` (default): any drift invalidates the whole flow; an
    administrator must get a fresh proposal. This is the safe default
    because the freshness check is only meaningful when the
    fingerprint covers the whole item: with a field-subset
    fingerprint, "no drift" only guarantees the fields outside the
    subset are intact, and merging the proposal into the live object
    could then apply values nobody ever approved (the REST PUT merge
    semantics, see ``ManagedRestItem`` serialization, also wipe
    omitted instance fields on partial payloads).

    ``FORCE``: proceed, overwriting concurrent changes. Only sound for
    action types whose ``execute`` is an idempotent *set* of the full
    approved payload (multi-step flows, or a future PATCH-semantics
    route) — never for the current merge-based PUT handlers. It is an
    explicit per-type declaration reviewed in code, not a parameter an
    admin can choose at approval time.
    """

    DENY = "deny"
    FORCE = "force"


if typing.TYPE_CHECKING:
    from uds.models import FlowAction

SECRET_FIELD_TYPES: typing.Final[frozenset[types.ui.FieldType]] = frozenset(
    {types.ui.FieldType.PASSWORD, types.ui.FieldType.HIDDEN}
)
REDACTED: typing.Final[str] = "(redacted)"


class MutableActionType(abc.ABC):
    """One mutable action (e.g. ``provider.update``) in the registry."""

    type_id: typing.ClassVar[str]
    """Stable identifier, e.g. ``provider.update``."""

    title: typing.ClassVar[str]
    """Human title for the generated proposal tool."""

    description: typing.ClassVar[str]
    """Base description for the generated proposal tool."""

    handler: typing.ClassVar[type[Handler]]
    """REST handler whose canonical operation executes on approval."""

    target_scoped_fields: typing.ClassVar[bool] = False
    """True when field definitions depend on the concrete target.

    For detail types (e.g. ``service.update``) the gui is built from the
    parent item plus the subtype, so discovery needs a ``target_uuid``
    and ``get_mutable_fields`` refuses a bare ``for_type``.
    """

    stale_policy: typing.ClassVar[StalePolicy] = StalePolicy.DENY
    """Approval-time behaviour when the target drifted from ``base_etag``.

    Defaults to :attr:`StalePolicy.DENY`: any drift invalidates the
    flow. A single ``FORCE`` type is an explicit per-action-type
    declaration (class attribute, code-reviewed); there is no
    per-flow or per-admin override.
    """

    # ------------------------------------------------------------- hooks

    @abc.abstractmethod
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        """Return the target model instance, or raise a REST NotFound."""

    @abc.abstractmethod
    def for_type_of(self, target: db_models.Model) -> str:
        """Return the ``data_type`` (subtype) of the target."""

    @abc.abstractmethod
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        """Ordered mutable field definitions for one subtype.

        Each definition carries at least ``name``, ``type`` (gui field
        type) and ``secret``; the rest of the gui metadata (label,
        tooltip, choices, ranges, ...) is passed through for the agent
        and the admin interface. This mirrors what the equivalent admin
        form and the REST ``gui`` endpoint expose.

        ``target`` carries the resolved model when available; types with
        ``target_scoped_fields`` build their definitions from it (their
        gui depends on the parent item, not only on the subtype) and
        refuse when it is ``None``.
        """

    @abc.abstractmethod
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        """Current values of ``names`` on ``target`` (CAS base)."""

    @abc.abstractmethod
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        """Field names the whole-item fingerprint covers.

        Must match what the handler's own ETag covers so a fresh
        fingerprint is comparable to the REST one. ``target`` follows
        the same rule as :meth:`field_definitions`.
        """

    @abc.abstractmethod
    def fingerprint(self, target: db_models.Model) -> str:
        """Current whole-item fingerprint (CAS soft-notice base)."""

    @abc.abstractmethod
    async def execute(self, action: "FlowAction", request: typing.Any) -> str:
        """Apply the approved action through the canonical REST machinery.

        Runs as the approving administrator (the request owner), so
        validation, audit and permissions are the standard ones.
        Returns a human summary stored on the action.
        """

    # ------------------------------------------------- generic CAS layer

    def permission_target(self, target: db_models.Model) -> db_models.Model:
        """Model whose ownership grants permission to execute the action.

        Defaults to the target itself. Detail types (e.g. services)
        inherit their permissions from their parent, so they return the
        parent here; flows check MANAGEMENT over the returned model.
        """
        return target

    def secret_names(self, for_type: str, target: db_models.Model | None = None) -> set[str]:
        """Names of the secret fields of one subtype."""
        return {d["name"] for d in self.field_definitions(for_type, target) if d.get("secret")}

    @staticmethod
    def _field_type_errors(name: str, value: typing.Any, definition: JsonObject) -> list[str]:
        """Basic json-side type checks of one field value against its definition."""
        field_type = types.ui.FieldType.from_str(str(definition.get("type", "")))
        if field_type == types.ui.FieldType.NUMERIC and not isinstance(value, (int, float)):
            return [f"field {name} must be numeric"]
        if field_type == types.ui.FieldType.CHECKBOX and not isinstance(value, bool):
            return [f"field {name} must be a boolean"]
        if field_type in (
            types.ui.FieldType.TAGLIST,
            types.ui.FieldType.MULTICHOICE,
            types.ui.FieldType.EDITABLELIST,
        ) and not isinstance(value, (list, tuple)):
            return [f"field {name} must be a list"]
        return []

    def flatten_values(self, values: JsonObject) -> JsonObject:
        """Flat view of a proposed payload (identity by default).

        Types that nest part of their payload (e.g. ``instance.*``) use
        dotted names here, so the CAS snapshots, masking and diffs work
        on one uniform keyspace.
        """
        return dict(values)

    def validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        """Validate a proposed (flat) payload against the field definitions.

        Returns a list of problems (empty when valid). Unknown fields
        are rejected with the accepted names listed, so an agent can
        self-correct without an extra discovery round-trip.
        """
        definitions = {d["name"]: d for d in self.field_definitions(for_type, target)}
        flat = self.flatten_values(values)
        errors: list[str] = []
        for name, value in flat.items():
            definition = definitions.get(name)
            if definition is not None:
                errors.extend(self._field_type_errors(name, value, definition))
        unknown = sorted(set(flat) - set(definitions))
        if unknown:
            accepted = ", ".join(sorted(definitions))
            errors.append(f"unknown fields: {', '.join(unknown)} (accepted: {accepted})")
        return errors

    def describe(self, action: "FlowAction") -> JsonObject:
        """Representation for the agent (secrets masked).

        If the target (or its type) has vanished, every value is masked:
        a broken lookup must not become a leak.
        """
        flat = self.flatten_values(action.values)
        secrets: set[str] = set()
        try:
            target = self.resolve_target(action.target_uuid)
            secrets = self.secret_names(self.for_type_of(target), target)
        except Exception:
            secrets = set(flat)
        return {
            "type": action.action_type,
            "target": action.target_uuid,
            "status": action.status,
            "changes": {name: (REDACTED if name in secrets else flat.get(name)) for name in sorted(flat)},
            "secrets_changed": sorted(set(flat) & secrets),
        }

    def diff(self, action: "FlowAction") -> JsonObject:
        """Fresh representation for the administrator (CAS checked now).

        Secrets are included: visibility is decided by the admin
        interface, never re-exposed to the agent.
        """
        target = self.resolve_target(action.target_uuid)
        flat = self.flatten_values(action.values)
        current = self.snapshot_values(target, flat)
        stale_fields = sorted(
            name
            for name, proposed_base in action.base_values.items()
            if name in current and current[name] != proposed_base
        )
        item_changed = self.fingerprint(target) != action.base_etag
        return {
            "type": action.action_type,
            "target": action.target_uuid,
            "status": action.status,
            "proposed": action.values,
            "current": current,
            "stale_fields": stale_fields,
            "item_changed": item_changed,
        }

    def snapshot_and_fingerprint(self, target: db_models.Model, values: JsonObject) -> tuple[JsonObject, str]:
        """Server-side CAS snapshots taken when a proposal is created."""
        return (
            self.snapshot_values(target, self.flatten_values(values)),
            self.fingerprint(target),
        )

    def _path(self) -> str:
        return self.type_id.split(".")[0] + "s"
