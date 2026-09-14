"""Shared machinery for pure many-to-many relation action types.

``M2MSetActionType`` covers the relation types whose rows carry no
per-relation data (unlike ``metapool.members.set``, which stores priority
and enabled per row): the relation IS the set of related uuids
(``assignedGroups``, ``transports``). Option A applies unchanged: the
proposal carries the *complete desired set*, never a delta, and on
approval it is diffed against the live set.

Execution goes through the canonical detail surfaces of each handler
(``{collection}/{uuid}/groups``, ``{collection}/{uuid}/transports``):
POST ``{"id": uuid}`` adds a member, DELETE on the member uuid removes
it. A Django M2M has a unique row per pair, so there is no "edit" and
no live duplicates to defend against. Stale policy is DENY (as always):
the whole-set fingerprint must still match at approval time.
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

JsonObject = dict[str, typing.Any]

# (method, detail args, params) of one REST operation of the diff.
# POST carries no positional arg (the member id travels in params["id"],
# and a positional arg would dispatch as a custom method instead of a
# create); DELETE identifies the member positionally, as the handler's
# ``delete_item`` expects.
M2MOperation = tuple[types.rest.CustomMethodMethod, tuple[str, ...], JsonObject]


class M2MSetActionType(mutability_base.MutableActionType):
    """Proposal: set the complete desired many-to-many related set."""

    # Subclasses MUST set, besides the usual handler/model/noun:
    #   field_name        name of the single multichoice field ("groups")
    #   field_label       human label for the field
    #   field_tooltip     guidance for the agent (semantics of the set)
    #   item_label        singular kind for error messages ("group")
    #   related_model     model of the related items (existence checks)
    #   include_choices   publish the uuid->name choices in discovery
    #                     (False when the universe can be huge: LDAP)
    #   relation_manager  attribute of the target holding the M2M
    #   detail_handler    DetailHandler implementing the add/remove verbs
    #   detail_path       collection path with the {uuid} parent placeholder
    #   parent_collection REST collection of the parent handler

    field_name: typing.ClassVar[str]
    field_label: typing.ClassVar[str]
    field_tooltip: typing.ClassVar[str]
    item_label: typing.ClassVar[str]
    related_model: typing.ClassVar[type[models.Group] | type[models.Transport]]
    include_choices: typing.ClassVar[bool] = True
    relation_manager: typing.ClassVar[str]
    detail_handler: typing.ClassVar[type[DetailHandler[typing.Any]]]
    detail_path: typing.ClassVar[str]
    parent_collection: typing.ClassVar[str]
    # Target model and static subtype of the parent item (the relation
    # itself is not module-backed)
    model: typing.ClassVar[type[models.MetaPool] | type[models.ServicePool]]
    subtype: typing.ClassVar[str]
    noun: typing.ClassVar[str]

    # Discovery without the target is useless: the agent must see the
    # current set before it can propose the final one.
    target_scoped_fields = True

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return self.model.objects.get(uuid__iexact=target_uuid)
        except ObjectDoesNotExist:
            raise rest_exceptions.NotFound(f"{self.noun} not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return self.subtype

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        definition: JsonObject = {
            "name": self.field_name,
            "type": types.ui.FieldType.MULTICHOICE.value,
            "label": self.field_label,
            "tooltip": self.field_tooltip,
            "secret": False,
        }
        if self.include_choices:
            definition["choices"] = [
                {"value": str(uuid), "label": name}
                for uuid, name in self.related_model.objects.order_by("name").values_list("uuid", "name")
            ]
        return [definition]

    @typing.override
    def validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        errors = super().validate_values(for_type, values, target)
        flat = self.flatten_values(values)
        if self.field_name not in flat:
            errors.append(
                f"field {self.field_name} is required (the complete desired set; use [] to remove all)"
            )
            return errors
        errors.extend(self._validate_members(flat[self.field_name]))
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

    # ---------------------------------------------------------- execution

    @typing.override
    async def execute(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # Plan the diff in one sync boundary (target + live set), then run
        # the REST operations. The whole-set CAS check (stale policy DENY)
        # already validated the live set against the approved base.
        def _plan() -> tuple[str, list[M2MOperation]]:
            target = self.resolve_target(action.target_uuid)
            current = set(self._live_uuids(target))
            desired = {
                process_uuid(typing.cast(str, raw))
                for raw in typing.cast("collections.abc.Sequence[typing.Any]", action.values[self.field_name])
            }
            operations: list[M2MOperation] = [
                (types.rest.CustomMethodMethod.DELETE, (member_uuid,), {})
                for member_uuid in sorted(current - desired)
            ]
            operations += [
                (types.rest.CustomMethodMethod.POST, (), {"id": member_uuid})
                for member_uuid in sorted(desired - current)
            ]
            return self.target_display_name(target), operations

        name, operations = await sync_to_async(_plan, thread_sensitive=True)()

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
