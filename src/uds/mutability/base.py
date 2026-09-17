"""Abstract definition of a mutable action type.

A ``MutableActionType`` declares one well-scoped mutation an agent may
*propose* (never apply directly): which REST handler owns it, which
fields are mutable (from the same gui definitions the admin forms use),
and how to execute it through the canonical REST machinery once an
administrator approves it.

The generic CAS machinery (server-side snapshots, stale detection,
descriptions for the agent and fresh diffs for the administrator) lives
here so every concrete type only implements the small domain hooks.

CAS (compare-and-swap) is the optimistic-concurrency scheme every
proposal over an EXISTING target rides: creating a proposal freezes
server-side snapshots of the live target (the current values of the
proposed fields plus a whole-item fingerprint, ``base_values``/
``base_etag``), and approval and execution re-check the live target
against those snapshots. Drift resolves through :class:`StalePolicy` —
``DENY`` invalidates the flow, ``FORCE`` proceeds — so an administrator
never approves (nor executes) an action over a target that no longer
looks like what the agent saw. Creation proposals (``op_create``) are
the exception: there is no target to freeze, so they carry no CAS base
at all; the idempotence of the flow lifecycle (an executed action never
re-runs) is their safety net.
"""

import abc
import collections.abc
import enum
import typing

from django.db import models as db_models

from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.core.util import permissions
from uds.models import User
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


class ActionOperation(enum.StrEnum):
    """The fixed vocabulary of write operations an action type may expose.

    A single :class:`MutableActionType` declares only a *root*
    ``type_id`` (e.g. ``servicepool.group``); the operations it
    implements are *derived from the hooks it overrides* (see
    :meth:`MutableActionType.supported_operations`), and the registry
    expands them into one full id per operation
    (``servicepool.group.set``, ``.add``, ``.delete``), each registered
    as a zero-arg factory that binds the operation on the instance. This
    lets one class serve several full ids without a class per verb and
    without a declaration that could drift from the code.

    The names mirror HTTP so the payload semantics are predictable:
    ``update`` patches scalar fields, ``set`` replaces a whole relation,
    ``add``/``delete`` apply to the listed members only. ``create`` is
    the creation verb: there is no target yet (no CAS to freeze), the
    proposal carries the subtype plus the initial values, and the real
    uuid is assigned at execution. ``custom`` is the open verb for
    entity-scoped actions: the family declares a CHOICE field naming the
    verb to perform (reset, and future ones), so growing *those* is
    data, not a new operation. Reads are not operations: the live view
    of an entity is served by the synchronous
    :meth:`EntityDescriptor.read` on the shared descriptor base, never
    through the flow machinery.
    """

    UPDATE = "update"
    SET = "set"
    ADD = "add"
    DELETE = "delete"
    CREATE = "create"
    CUSTOM = "custom"

    def as_str(self) -> str:
        """Plain string form for joining into a full ``type_id``."""
        return self.value


#: Operations dispatchable through ``execute``, mapped to the ``op_*``
#: hook whose presence declares them. ``custom`` carries a family-defined
#: verb as a CHOICE payload field (e.g. resetting an assigned user
#: service), so future entity-scoped verbs are data, not new operations.
#: Reads are not operations at all — they belong to
#: :class:`EntityDescriptor`, outside this enum.
_OPERATION_HOOKS: typing.Final[dict["ActionOperation", str]] = {
    ActionOperation.UPDATE: "op_update",
    ActionOperation.SET: "op_set",
    ActionOperation.ADD: "op_add",
    ActionOperation.DELETE: "op_delete",
    ActionOperation.CREATE: "op_create",
    ActionOperation.CUSTOM: "op_custom",
}

CREATE_TARGET_UUID: typing.Final[str] = "00000000-0000-0000-0000-000000000000"
"""Sentinel ``target_uuid`` of root creation proposals.

A creation has no target yet: root creates carry this sentinel as
``FlowAction.target_uuid`` (detail creates carry the parent uuid, which
is a real target for permissions and display). The real uuid is assigned
by the REST create at execution and returned in the action result.
"""


def _json_safe(value: typing.Any) -> typing.Any:
    """Coerce a structure into plain JSON-serializable data.

    Field definitions carry lazy-translation proxies (labels) and the
    review snapshot may carry datetimes or decimals: the Properties
    store (a plain JSONField) cannot serialize those, so anything that
    is not a JSON scalar, list or dict is stringified.
    """
    if isinstance(value, dict):
        source = typing.cast("dict[typing.Any, typing.Any]", value)
        return {str(k): _json_safe(v) for k, v in source.items()}
    if isinstance(value, list):
        items = typing.cast("list[typing.Any]", value)
        return [_json_safe(v) for v in items]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class EntityDescriptor(abc.ABC):
    """The read-only identity and discovery surface of one entity type.

    A descriptor declares what the entity *is* — not what may be mutated
    on it: the root ``type_id`` (singular, e.g. ``provider`` or
    ``servicepool.group``), how to resolve and subtype a target, and the
    field definitions / current values / fingerprint that both the
    discovery surface and the CAS layer are built from. It also owns the
    synchronous :meth:`read` view, so the MCP ``get_*`` tools and any
    future read surface publish exactly one shape.

    :class:`MutableActionType` *is* an ``EntityDescriptor`` that can also
    propose and execute writes; pure read types (curated entities with
    no mutations) inherit just this base. Nothing here knows about
    flows, approvals or operations.
    """

    type_id: typing.ClassVar[str]
    """Root identifier, e.g. ``provider`` or ``servicepool.group``."""

    noun: typing.ClassVar[str] = "item"
    """Human kind of the target (the model behind type_id): used by
    generated tool text and error messages (``Service pool``, ``Server``)."""

    target_scoped_fields: typing.ClassVar[bool] = False
    """True when field definitions depend on the concrete target.

    For detail types (e.g. ``service.update``) the gui is built from the
    parent item plus the subtype, so discovery needs a ``target_uuid``
    and ``get_mutable_fields`` refuses a bare ``for_type``.
    """

    @abc.abstractmethod
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        """Return the target model instance, or raise a REST NotFound."""

    def target_uuid_of(self, target: db_models.Model) -> str:
        """Identifier of ``target`` for proposals (inverse of resolve).

        Defaults to the model uuid. Types whose target has no uuid
        (configuration: ``Section.key``) override this.
        """
        return typing.cast("str", getattr(target, "uuid", None))

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

    def read(self, target_uuid: str) -> JsonObject:
        """Live read view of the target (no flow, no approval, no execution).

        The generic view mirrors the discovery surface: the field
        definitions of the concrete target plus its current values.
        Relation types override it to add the human side of the current
        members. Overriding ``read`` is what publishes the entity's
        ``get_*`` tool: implementation is declaration here too.
        """
        target = self.resolve_target(target_uuid)
        for_type = self.for_type_of(target)
        fields = self.field_definitions(for_type, target)
        return {
            "entity_type": self.type_id,
            "target_uuid": self.target_uuid_of(target),
            "for_type": for_type,
            "fields": fields,
            "current_values": self.snapshot_values(target, [d["name"] for d in fields]),
        }

    @classmethod
    def readable(cls) -> bool:
        """True when this descriptor publishes a live read (``get_*`` tool)."""
        return cls.read is not EntityDescriptor.read

    def read_title(self) -> str:
        """Human title of the generated read tool (when :meth:`readable`)."""
        return f"Read {self.noun.lower()}"

    def read_description(self) -> str:
        """Description of the generated read tool (when :meth:`readable`)."""
        return (
            f"Read one {self.noun.lower()} by uuid: its mutable field definitions and "
            "current values. Applies immediately; it is a plain read."
        )

    def permission_target(self, target: db_models.Model) -> db_models.Model:
        """Model whose ownership grants permission to act on the target.

        Defaults to the target itself. Detail types (e.g. services)
        inherit their permissions from their parent, so they return the
        parent here; flows check MANAGEMENT over the returned model.
        """
        return target

    def secret_names(self, for_type: str, target: db_models.Model | None = None) -> set[str]:
        """Names of the secret fields of one subtype."""
        return {d["name"] for d in self.field_definitions(for_type, target) if d.get("secret")}


class MutableActionType(EntityDescriptor):
    """One mutable action family in the registry: writes over a descriptor.

    A concrete class declares a *root* ``type_id`` (``provider``,
    ``servicepool.group``) and implements the ``op_*`` hooks it supports;
    its write operations are *derived from those overrides* (see
    :meth:`supported_operations` — implementing a verb is declaring it).
    The registry expands each operation into a full id binding
    (``provider.update``, ``servicepool.group.set``, ``.add``, ...). One
    instance serves one full id: it carries the bound :attr:`operation`
    and the complete :attr:`full_id`, and :meth:`execute` dispatches to
    the matching ``op_*`` implementation. Call sites use
    ``registry.get(full_id)()`` exactly as before — the registry value
    is a zero-arg factory that binds the operation.
    """

    operation: "ActionOperation | None" = None
    """The operation this instance is bound to (set by the registry factory)."""

    title: typing.ClassVar[str]
    """Human title for the generated proposal tool (single-op families)."""

    description: typing.ClassVar[str]
    """Base description for the generated proposal tool (single-op families)."""

    handler: typing.ClassVar[type[Handler]]
    """REST handler whose canonical operation executes on approval."""

    stale_policy: typing.ClassVar[StalePolicy] = StalePolicy.DENY
    """Approval-time behaviour when the target drifted from ``base_etag``.

    Defaults to :attr:`StalePolicy.DENY`: any drift invalidates the
    flow. A ``FORCE`` policy is an explicit per-action-type declaration
    (class attribute or :attr:`stale_policies_overrides` entry,
    code-reviewed); there is no per-flow or per-admin override.
    """

    stale_policies_overrides: typing.ClassVar[dict["ActionOperation", StalePolicy]] = {}
    """Per-operation overrides of :attr:`stale_policy` (multi-operation families).

    :meth:`get_stale_policy` resolves the policy of the operation the
    binding carries through this map; operations not listed fall back to
    :attr:`stale_policy`. This lets the delta operations
    (``add``/``delete``, idempotent against concurrent edits of *other*
    members) run FORCE while whole-set ``set``/``update`` stay DENY in
    the same family.
    """

    create_needs_parent: typing.ClassVar[bool] = False
    """Create-family declaration: the creation happens inside a container.

    ``False`` (default): the family creates first-level entities (a
    provider, a server group); proposals carry
    :data:`CREATE_TARGET_UUID` as target and propose-access is the root
    ALL check (mirrors the REST create: administrators, or an explicit
    type-level ALL grant).

    ``True``: the family creates details of a container (a service of a
    provider, a server of a server group); the proposal targets the
    PARENT uuid and propose-access is MANAGEMENT over it, exactly the
    REST detail-create semantics.
    """

    _RESERVED_CREATE_KEYS: typing.ClassVar[frozenset[str]] = frozenset({"data_type"})
    """Proposal keys consumed by the create machinery itself (the subtype
    being created), never validated against the field definitions."""

    def __init__(self, operation: "ActionOperation | None" = None) -> None:
        """Bind one instance to one operation of the family.

        ``operation`` defaults to the single implemented operation when
        the family has exactly one, so direct instantiation keeps
        working; registry factories always pass the full-id operation
        explicitly. Multi-operation families stay unbound (raising on
        use) unless an operation is given.
        """
        if operation is None:
            supported = self.supported_operations()
            if len(supported) == 1:
                (operation,) = supported
        elif operation not in self.supported_operations():
            raise ValueError(f"{self.type_id} does not support the {operation.as_str()} operation")
        self.operation = operation

    @classmethod
    def supported_operations(cls) -> frozenset["ActionOperation"]:
        """Write operations this family implements, derived from its overrides.

        A verb is supported exactly when the class replaces the
        corresponding rejecting ``op_*`` stub of this base (the
        ``is not`` comparison also counts inherited intermediate
        implementations, e.g. the module-backed ``op_update``).
        Implementation is declaration — there is nothing to keep in
        sync. Reads are not operations: the live view belongs to the
        descriptor base (see :meth:`EntityDescriptor.readable`).
        """
        return frozenset(
            operation
            for operation, hook in _OPERATION_HOOKS.items()
            if getattr(cls, hook) is not getattr(MutableActionType, hook)
        )

    def get_stale_policy(self) -> StalePolicy:
        """Stale policy resolved for the operation bound to this instance."""
        if self.operation is not None:
            policy = self.stale_policies_overrides.get(self.operation)
            if policy is not None:
                return policy
        return self.stale_policy

    @property
    def full_id(self) -> str:
        """Complete registry id of this instance (root id + operation)."""
        if self.operation is None:
            return self.type_id
        return f"{self.type_id}.{self.operation.as_str()}"

    def tool_title(self) -> str:
        """Human title of the MCP tool for the bound operation.

        Single-operation families inherit the class ``title``; multi-op
        families override to phrase each verb.
        """
        return self.title

    def tool_description(self) -> str:
        """Description of the MCP tool for the bound operation."""
        return self.description

    # ------------------------------------------------------------- hooks

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

    async def execute(self, action: "FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        """Apply the approved action through the operation bound to this instance.

        The registry binds every instance to exactly one
        :class:`ActionOperation` (its full id ends in it), and this
        launcher dispatches to the matching ``op_*`` implementation.
        Types only implement the ``op_*`` hooks they support; the
        defaults below reject the rest, so an unsupported operation fails
        loudly (never silently) if a persisted action outlives a code
        change that removed it.
        """
        op = self.operation
        if op is None:
            raise NotImplementedError(f"{self.type_id}: unbound action type (registry bug)")
        hook = _OPERATION_HOOKS.get(op)
        if hook is None:
            raise NotImplementedError(f"{self.full_id}: unsupported operation")
        implementation: collections.abc.Callable[
            [FlowAction, ExtendedHttpRequestWithUser], collections.abc.Awaitable[str]
        ] = getattr(self, hook)
        return await implementation(action, request)

    async def op_update(self, action: "FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        raise NotImplementedError(f"{self.full_id}: does not support the update operation")

    async def op_set(self, action: "FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        raise NotImplementedError(f"{self.full_id}: does not support the set operation")

    async def op_add(self, action: "FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        raise NotImplementedError(f"{self.full_id}: does not support the add operation")

    async def op_delete(self, action: "FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        raise NotImplementedError(f"{self.full_id}: does not support the delete operation")

    async def op_create(self, action: "FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        raise NotImplementedError(f"{self.full_id}: does not support the create operation")

    async def op_custom(self, action: "FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        raise NotImplementedError(f"{self.full_id}: does not support the custom operation")

    # ------------------------------------------------- generic CAS layer

    def check_propose_access(self, user: User, target: db_models.Model) -> None:
        """Authorization to *propose* this action over ``target``.

        The MANAGEMENT permission is for the wrapped types (the targets:
        providers, services, ...), never for the flow itself. Types
        whose target has no permission model (configuration) override
        this hook (configuration: superuser only). Raises AccessDenied.
        """
        if not permissions.has_access(
            user, self.permission_target(target), types.permissions.PermissionType.MANAGEMENT
        ):
            raise rest_exceptions.AccessDenied()

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
        return self._validate_against(definitions, values)

    def create_for_type(self, values: JsonObject) -> str:
        """Subtype a creation proposal instantiates (reserved ``data_type`` key)."""
        return str(values.get("data_type", ""))

    def resolve_create_parent(self, parent_uuid: str) -> db_models.Model:
        """Resolve the CONTAINER of a detail creation proposal.

        Only meaningful for families declaring
        :attr:`create_needs_parent`: the proposal targets the parent
        uuid, which is a model of the PARENT kind (a provider for
        services, a server group for servers) — never resolvable
        through :meth:`resolve_target`, which looks up the family's own
        entity.
        """
        raise NotImplementedError(f"{self.full_id}: family does not create inside a container")

    def create_field_definitions(
        self,
        for_type: str,
        target: db_models.Model | None = None,
    ) -> list[JsonObject]:
        """Field definitions of a creation of one subtype.

        ``target`` is the parent container when
        :attr:`create_needs_parent` is set (whose gui may leak into the
        detail's), ``None`` otherwise. Defaults to the plain definitions
        without a live instance; families whose gui depends on the
        parent override this.
        """
        return self.field_definitions(for_type, None)

    def create_validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        """Validate a creation proposal against the creation definitions.

        ``for_type`` must be a non-empty subtype; the reserved machinery
        keys (``data_type``) are consumed before the definition checks.
        """
        if not for_type.strip():
            return ["values must declare the data_type to create"]
        payload = {k: v for k, v in values.items() if k not in self._RESERVED_CREATE_KEYS}
        definitions = {d["name"]: d for d in self.create_field_definitions(for_type, target)}
        return self._validate_against(definitions, payload)

    def check_create_access(self, user: "User", parent: db_models.Model | None = None) -> None:
        """Authorization to *propose* a creation.

        Detail creations (``create_needs_parent``) check MANAGEMENT
        directly over the resolved parent — the container itself, never
        the ``permission_target`` hop (that one assumes the family's own
        entity, which does not exist yet). Root creations check ``ALL``
        over the model type at root, the exact REST create rule:
        administrators pass, and so do explicit type-level ``ALL``
        grants. Raises AccessDenied.
        """
        if parent is not None:
            if not permissions.has_access(user, parent, types.permissions.PermissionType.MANAGEMENT):
                raise rest_exceptions.AccessDenied()
            return
        handler_class = typing.cast("type[typing.Any]", self.handler)
        model_class = typing.cast("type[db_models.Model]", handler_class.MODEL)
        if not permissions.has_access(user, model_class(), types.permissions.PermissionType.ALL, True):
            raise rest_exceptions.AccessDenied()

    def _validate_against(
        self,
        definitions: dict[str, JsonObject],
        values: JsonObject,
    ) -> list[str]:
        """Shared payload checks against one prebuilt definition map."""
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
        a broken lookup must not become a leak. Creation proposals have
        no live target (the parent, for details): the secrets come from
        the declared subtype, masked wholesale if even that fails.
        """
        flat = self.flatten_values(action.values)
        secrets: set[str] = set()
        try:
            if self.operation is ActionOperation.CREATE:
                for_type = self.create_for_type(flat)
                target = self.resolve_create_parent(action.target_uuid) if self.create_needs_parent else None
                secrets = {
                    d["name"] for d in self.create_field_definitions(for_type, target) if d.get("secret")
                }
            else:
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
        interface, never re-exposed to the agent. Creation proposals
        have no live state to compare against (no CAS): only the
        proposed payload is shown, with empty drift keys.
        """
        flat = self.flatten_values(action.values)
        if self.operation is ActionOperation.CREATE:
            return {
                "type": action.action_type,
                "target": action.target_uuid,
                "status": action.status,
                "proposed": action.values,
                "current": {},
                "stale_fields": [],
                "item_changed": False,
            }
        target = self.resolve_target(action.target_uuid)
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

    def target_display_name(self, target: db_models.Model) -> str:
        """Human name of the target for the admin review cache.

        Defaults to ``str(target)``; types with a friendlier label (e.g.
        the provider name) may override it.
        """
        return str(target)

    def approval_snapshot(self, action: "FlowAction") -> JsonObject:
        """Display cache frozen when an administrator approves an action.

        Stored on ``FlowAction.snap_info`` (Properties) so the admin diff
        survives deletion of the target. ``base_values``/``base_etag`` stay
        immutable: this is the *live* view at approval time, never the CAS
        base. Creation proposals freeze the declared subtype's field
        definitions only (there is no live state to snapshot).
        """
        flat = self.flatten_values(action.values)
        if self.operation is ActionOperation.CREATE:
            for_type = self.create_for_type(flat)
            target = self.resolve_create_parent(action.target_uuid) if self.create_needs_parent else None
            target_name = f"new {self.noun.lower()}"
            if target is not None:
                target_name += f" in {self.target_display_name(target)}"
            return {
                "target_name": target_name,
                "for_type": for_type,
                "current_values": {},
                "fields": _json_safe(self.create_field_definitions(for_type, target)),
            }
        target = self.resolve_target(action.target_uuid)
        for_type = self.for_type_of(target)
        return {
            "target_name": self.target_display_name(target),
            "for_type": for_type,
            "current_values": _json_safe(self.snapshot_values(target, list(flat))),
            "fields": _json_safe(self.field_definitions(for_type, target)),
        }

    def field_drift(self, action: "FlowAction", *, ref_values: JsonObject) -> JsonObject:
        """Touched fields whose live value differs from a frozen reference.

        Returns ``{field: {"approved": ..., "live": ...}}``, empty when
        every field this action modifies still matches ``ref_values``.
        Only the touched fields are considered: unrelated changes to the
        target never block execution (second-pass check of the two-pass
        launch; the first pass is the strict whole-item one in
        :meth:`FlowStore.verify_flow`).
        """
        target = self.resolve_target(action.target_uuid)
        flat = self.flatten_values(action.values)
        live = self.snapshot_values(target, list(flat))
        return {
            name: {"approved": ref_values.get(name), "live": live[name]}
            for name in flat
            if name in live and live[name] != ref_values.get(name)
        }

    def drift_against(self, action: "FlowAction", *, ref_etag: str, ref_values: JsonObject) -> str:
        """Compliance of the live target against one reference snapshot.

        Returns ``"ok"`` (item untouched), ``"outdated"`` (the item changed
        but none of the fields this action touches did) or ``"conflict"``
        (a touched field changed, or the target cannot be verified). The
        reference is chosen by the caller (proposal base while pending,
        the frozen approval once approved/revoked).
        """
        try:
            target = self.resolve_target(action.target_uuid)
        except Exception:
            return "conflict"  # unverifiable is never "ok"
        if self.fingerprint(target) == ref_etag:
            return "ok"
        try:
            drifted = self.field_drift(action, ref_values=ref_values)
        except Exception:
            return "conflict"
        return "conflict" if drifted else "outdated"
