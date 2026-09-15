"""Shared machinery for pure many-to-many relation action types.

``M2MRelationActionType`` covers the relation types whose rows carry no
per-relation data (unlike ``metapool.members``, which stores priority and
enabled per row): the relation IS the set of related uuids
(``assignedGroups``, ``transports``). One class family serves four
full ids:

``set``     — complete desired set (option A: never a delta); on approval
              it is diffed against the live set. ``[]`` clears the relation.
``add``     — the listed members only; already-attached ones are skipped.
``delete``  — the listed members only; absent ones are skipped.
``get``     — read the current related set back (uuids and names). Never
              proposed, never executed (the tool answers directly).

``set`` keeps the DENY stale policy (the whole-set fingerprint must still
match at approval time). The delta operations are idempotent against
concurrent edits of *other* members — their execution re-reads the live
set and touches only the approved members — so they run under FORCE.

Execution goes through the canonical detail surfaces of each handler
(``{collection}/{uuid}/groups``, ``{collection}/{uuid}/transports``):
POST ``{"id": uuid}`` adds a member, DELETE on the member uuid removes
it. A Django M2M has a unique row per pair, so there is no "edit" and
no live duplicates to defend against.
"""

import collections.abc
import hashlib
import json
import typing

from asgiref.sync import sync_to_async
from django.core.exceptions import ObjectDoesNotExist
from django.db import models as db_models

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.core.util.model import process_uuid
from uds.mcp.rest_proxy import RestProxy, RestTarget
from uds.REST.model import DetailHandler

from .. import base as mutability_base
from ..base import ActionOperation, StalePolicy

JsonObject = dict[str, typing.Any]

# (method, detail args, params) of one REST operation of the diff.
# POST carries no positional arg (the member id travels in params["id"],
# and a positional arg would dispatch as a custom method instead of a
# create); DELETE identifies the member positionally, as the handler's
# ``delete_item`` expects.
M2MOperation = tuple[types.rest.CustomMethodMethod, tuple[str, ...], JsonObject]


class M2MRelationActionType(mutability_base.MutableActionType):
    """One many-to-many relation family: set / add / delete / get.

    The four operations need no declaration: ``op_set``/``op_add``/
    ``op_delete`` implemented here plus the :meth:`read` override are
    exactly what the registry derives (implementation is declaration).
    """

    stale_policies: typing.ClassVar[dict[ActionOperation, StalePolicy]] = {
        ActionOperation.SET: StalePolicy.DENY,
        # Delta operations re-read the live set at execution and skip
        # members that no longer need the operation, so a concurrent
        # change elsewhere never clobbers it.
        ActionOperation.ADD: StalePolicy.FORCE,
        ActionOperation.DELETE: StalePolicy.FORCE,
    }

    # Subclasses MUST set, besides the usual handler/model/noun:
    #   field_name        name of the single multichoice field ("groups")
    #   field_label       human label for the field
    #   item_label        singular kind for error messages ("group")
    #   relation_label    human plural for the tool text ("access groups")
    #   related_model     model of the related items (existence checks)
    #   include_choices   publish the uuid->name choices in discovery
    #                     (False when the universe can be huge: LDAP)
    #   relation_manager  attribute of the target holding the M2M
    #   detail_handler    DetailHandler implementing the add/remove verbs
    #   detail_path       collection path with the {uuid} parent placeholder
    #   parent_collection REST collection of the parent handler
    #   uuid_source       where the member uuids come from (tool guidance)

    field_name: typing.ClassVar[str]
    field_label: typing.ClassVar[str]
    item_label: typing.ClassVar[str]
    relation_label: typing.ClassVar[str]
    related_model: typing.ClassVar[type[models.Group] | type[models.Transport]]
    include_choices: typing.ClassVar[bool] = True
    relation_manager: typing.ClassVar[str]
    detail_handler: typing.ClassVar[type[DetailHandler[typing.Any]]]
    detail_path: typing.ClassVar[str]
    parent_collection: typing.ClassVar[str]
    uuid_source: typing.ClassVar[str] = "the read tool of this relation"
    # Target model and static subtype of the parent item (the relation
    # itself is not module-backed)
    model: typing.ClassVar[type[models.MetaPool] | type[models.ServicePool]]
    subtype: typing.ClassVar[str]
    noun: typing.ClassVar[str]

    # Discovery without the target is useless: the agent must see the
    # current set before it can propose the final one.
    target_scoped_fields = True

    def _op(self) -> ActionOperation:
        """The operation this instance is bound to (relations have no default)."""
        if self.operation is None:
            raise ValueError(f"{self.type_id}: relation types must be bound to an operation")
        return self.operation

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return self.model.objects.get(uuid__iexact=target_uuid)
        except ObjectDoesNotExist:
            raise rest_exceptions.NotFound(f"{self.noun} not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return self.subtype

    # ------------------------------------------------- per-operation text

    def _tooltip(self) -> str:
        operation = self._op()
        noun_lower = self.noun.lower()
        if operation is ActionOperation.GET:
            return (
                f"Current {self.item_label} uuids attached to this {noun_lower} (read view; "
                "use the set/add/delete operations to change them)."
            )
        if operation is ActionOperation.SET:
            return (
                f"Complete desired set of {self.item_label} uuids for this {noun_lower}. Members "
                f"missing from the list are detached, new ones attached; [] detaches all. "
                f"{self._uuid_hint()}"
            )
        if operation is ActionOperation.ADD:
            return (
                f"{self.item_label.capitalize()} uuids to attach to this {noun_lower}. Only the "
                "listed ones are touched (already-attached members are skipped); the list must "
                f"not be empty. {self._uuid_hint()}"
            )
        return (
            f"{self.item_label.capitalize()} uuids to detach from this {noun_lower}. Only the "
            "listed ones are touched (absent members are skipped); the list must not be empty. "
            f"{self._uuid_hint()}"
        )

    def _uuid_hint(self) -> str:
        return f"Discover current members and {self.item_label} uuids with {self.uuid_source}."

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        definition: JsonObject = {
            "name": self.field_name,
            "type": types.ui.FieldType.MULTICHOICE.value,
            "label": self.field_label,
            "tooltip": self._tooltip(),
            "secret": False,
        }
        if self.include_choices:
            definition["choices"] = [
                {"value": str(uuid), "label": name}
                for uuid, name in self.related_model.objects.order_by("name").values_list("uuid", "name")
            ]
        return [definition]

    # -------------------------------------------------------- validation

    @typing.override
    def validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        errors = super().validate_values(for_type, values, target)
        flat = self.flatten_values(values)
        operation = self._op()
        if self.field_name not in flat:
            if operation is ActionOperation.SET:
                errors.append(
                    f"field {self.field_name} is required (the complete desired set; use [] to remove all)"
                )
            else:
                errors.append(
                    f"field {self.field_name} is required (the {operation.as_str()}d member uuids; "
                    "an empty list is not accepted, use the set operation with [] to remove all)"
                )
            return errors
        errors.extend(self._validate_members(flat[self.field_name]))
        if operation is not ActionOperation.SET:
            delta = flat[self.field_name]
            if isinstance(delta, (list, tuple)) and not delta:
                errors.append(
                    f"field {self.field_name} must list at least one uuid for "
                    f"{operation.as_str()} (use the set operation to clear all)"
                )
        return errors

    def _validate_members(self, value: typing.Any) -> list[str]:
        if not isinstance(value, (list, tuple)):
            return []  # base already flagged "must be a list"
        errors: list[str] = []
        seen: set[str] = set()
        seq = typing.cast("collections.abc.Sequence[typing.Any]", value)
        for index, raw in enumerate(seq):
            where = f"{self.field_name}[{index}]"
            if not isinstance(raw, str):
                errors.append(f"{where} must be the uuid string of an existing {self.item_label}")
                continue
            try:
                member_uuid = process_uuid(raw)
            except ValueError:
                errors.append(f"{where} is not a valid uuid: {raw!r}")
                continue
            if member_uuid in seen:
                errors.append(f"{where} duplicates {member_uuid} (each {self.item_label} can appear only once)")
                continue
            seen.add(member_uuid)
            if not self.related_model.objects.filter(uuid=member_uuid).exists():
                errors.append(f"{where} does not match any existing {self.item_label}: {member_uuid}")
        return errors

    # ------------------------------------------------------------ CAS

    def _live_uuids(self, target: db_models.Model) -> list[str]:
        manager = getattr(target, self.relation_manager)
        return sorted(str(uuid) for uuid in manager.values_list("uuid", flat=True))

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        return [self.field_name]

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        return {self.field_name: self._live_uuids(target)} if self.field_name in set(names) else {}

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        rows = self._live_uuids(target)
        return hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()

    @typing.override
    def target_display_name(self, target: db_models.Model) -> str:
        return typing.cast("typing.Any", target).name

    # ------------------------------------------------------------- read

    @typing.override
    def read(self, target_uuid: str) -> JsonObject:
        result = super().read(target_uuid)
        # The uuid list is already in ``current_values``; the read view
        # adds the human side of every live member.
        target = self.resolve_target(target_uuid)
        manager = getattr(target, self.relation_manager)
        rows: list[tuple[typing.Any, str]] = list(manager.values_list("uuid", "name").order_by("name"))
        result["current_members"] = [{"uuid": str(uuid), "name": name} for uuid, name in rows]
        return result

    # ---------------------------------------------------------- tool text

    @typing.override
    def tool_title(self) -> str:
        operation = self._op()
        noun_lower = self.noun.lower()
        if operation is ActionOperation.SET:
            return f"Propose {noun_lower} {self.relation_label}"
        if operation is ActionOperation.ADD:
            return f"Propose adding {self.relation_label} to a {noun_lower}"
        if operation is ActionOperation.DELETE:
            return f"Propose removing {self.relation_label} from a {noun_lower}"
        return f"Read {noun_lower} {self.relation_label}"

    @typing.override
    def tool_description(self) -> str:
        operation = self._op()
        noun_lower = self.noun.lower()
        if operation is ActionOperation.SET:
            return (
                f"Propose the complete desired set of {self.relation_label} of a {noun_lower}. The "
                f"'{self.field_name}' field is the FINAL set, not a delta: {self.item_label}s "
                "missing from it are detached, new ones attached. An empty list detaches every "
                f"{self.item_label}. Discover current members and uuids with the read tool "
                f"({self.uuid_source}). The proposal does NOT apply anything: it is queued until "
                "an administrator approves it."
            )
        if operation in (ActionOperation.ADD, ActionOperation.DELETE):
            verb = "attach" if operation is ActionOperation.ADD else "detach"
            skipped = (
                "already attached are skipped" if operation is ActionOperation.ADD else "absent are skipped"
            )
            return (
                f"Propose to {verb} the listed {self.relation_label} of a {noun_lower}. The "
                f"'{self.field_name}' field is a DELTA: only the listed {self.item_label}s are "
                f"touched, every other member keeps its state; {skipped}. The list must not be "
                f"empty. Discover current members and uuids with the read tool ({self.uuid_source}). "
                "The proposal does NOT apply anything: it is queued until an administrator "
                "approves it."
            )
        return (
            f"Read the current {self.relation_label} of one {noun_lower}: every member with its "
            f"uuid (to use in set/add/delete proposals) and name. Applies immediately; it is a "
            "plain read, never a proposal."
        )

    # ---------------------------------------------------------- execution

    def _plan(self, action: "models.FlowAction") -> tuple[str, list[M2MOperation]]:
        """Sync-boundary plan of the approved operation against the live set."""
        target = self.resolve_target(action.target_uuid)
        current = set(self._live_uuids(target))
        raw_rows = typing.cast("collections.abc.Sequence[typing.Any]", action.values[self.field_name])
        listed: set[str] = {process_uuid(typing.cast(str, raw)) for raw in raw_rows}
        operation = self._op()
        additions: set[str]
        removals: set[str]
        if operation is ActionOperation.SET:
            additions = listed - current
            removals = current - listed
        elif operation is ActionOperation.ADD:
            additions = listed - current
            removals = set()
        else:  # DELETE
            additions = set()
            removals = current & listed
        operations: list[M2MOperation] = [
            (types.rest.CustomMethodMethod.DELETE, (member_uuid,), {}) for member_uuid in sorted(removals)
        ]
        operations += [
            (types.rest.CustomMethodMethod.POST, (), {"id": member_uuid}) for member_uuid in sorted(additions)
        ]
        return self.target_display_name(target), operations

    async def _execute_op(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        name, operations = await sync_to_async(self._plan, thread_sensitive=True)(action)
        proxy = RestProxy()
        parent = RestTarget(typing.cast("typing.Any", self.handler), self.parent_collection)
        for method, args, params in operations:
            await proxy.execute(
                RestTarget(
                    typing.cast("typing.Any", self.detail_handler),
                    self.detail_path,
                    method,
                    args=args,
                    parent=parent,
                ),
                request,
                params,
                parent_uuid=action.target_uuid,
            )
        removed = sum(1 for method, _, _ in operations if method == types.rest.CustomMethodMethod.DELETE)
        added = sum(1 for method, _, _ in operations if method == types.rest.CustomMethodMethod.POST)
        return f'{self.noun} "{name}" {self.field_name}: {added} added, {removed} removed'

    @typing.override
    async def op_set(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The whole-set CAS check (stale policy DENY) already validated
        # the live set against the approved base before execution.
        return await self._execute_op(action, request)

    @typing.override
    async def op_add(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # FORCE by design: the live set is re-read and only the listed
        # members are touched (already-attached ones produce no operation).
        return await self._execute_op(action, request)

    @typing.override
    async def op_delete(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        return await self._execute_op(action, request)
