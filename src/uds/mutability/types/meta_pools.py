"""``metapool.update`` / ``metapool.members.set``: meta pool mutations.

Top level resource with a static gui (no module instance). Since the
gui-coupling trial, ``metapool.update`` derives its mutable surface from
``MetaPools.get_gui`` itself (the same definitions the admin form uses):
fields are proposable by default, FK references travelling on the
form-shaped PUT are annotated ``context`` on the gui and therefore join
the fingerprint but never the proposable surface. GUI changes propagate
to the agent automatically.

``metapool.members.set`` is the first *relation* action type: it proposes the
complete desired set of member pools (option A: full desired set, never a
delta), executed on approval as the minimal create/edit/delete diff
through the canonical ``meta_pools/{uuid}/pools`` detail surface.
"""

import collections.abc
import hashlib
import json
import typing
from types import SimpleNamespace as _Namespace

from asgiref.sync import sync_to_async
from django.db import models as db_models

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.core.util.model import process_uuid
from uds.mcp.rest_proxy import RestProxy, RestTarget
from uds.REST.methods.meta_pools import MetaPools
from uds.REST.methods.meta_service_pools import MetaServicesPool
from uds.REST.methods.user_services import Groups as AssignedGroups

from .. import base as mutability_base
from .. import gui_view
from ..etag import item_etag
from ._relations import M2MRelationActionType

JsonObject = dict[str, typing.Any]


def _metapool_gui_elements() -> list[types.ui.GuiElement]:
    """The admin gui of meta pools, ordered as the builder declared it."""
    shim = typing.cast(typing.Any, _Namespace())
    return sorted(MetaPools.get_gui(shim, "metapool"), key=lambda element: element.gui.order)


class MetaPoolUpdate(mutability_base.MutableActionType):
    """Proposal: update an existing meta pool."""

    type_id = "metapool"
    title = "Propose meta pool update"
    description = (
        "Propose changes to an existing meta pool (a pool that groups several "
        "service pools and selects one for each user). The proposal does NOT apply "
        "anything: it is queued until an administrator approves it. Mutable fields "
        "are name, short_name, comments, tags, visibility, selection policy, high "
        "availability policy, transport grouping and calendar message. The "
        "member pools, image and pool group cannot be changed through this "
        "proposal."
    )
    handler = MetaPools
    model = models.MetaPool
    noun = "Meta pool"

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.MetaPool.objects.get(uuid__iexact=target_uuid)
        except models.MetaPool.DoesNotExist:
            raise rest_exceptions.NotFound("Meta pool not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return "metapool"

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        # Agent view of the admin gui: only proposable fields are part of
        # the mutable surface (context references and relations stay out,
        # though context still travels in the fingerprint / PUT payload).
        return gui_view.agent_definitions(_metapool_gui_elements())

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        # Whole-item fingerprint: proposable fields plus the immutable
        # reference context the PUT requires (both come from the gui).
        return gui_view.fingerprint_names(_metapool_gui_elements())

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        meta_pool = typing.cast(models.MetaPool, target)
        wanted = set(names)
        # Immutable context: uuids as the PUT expects them ("-1" meaning
        # none, same convention the admin UI uses)
        context: JsonObject = {
            "image_id": meta_pool.image.uuid if meta_pool.image else "-1",
            "servicesPoolGroup_id": meta_pool.servicesPoolGroup.uuid if meta_pool.servicesPoolGroup else "-1",
        }
        columns: JsonObject = {
            "name": meta_pool.name,
            "short_name": meta_pool.short_name,
            "comments": meta_pool.comments,
            "tags": sorted(t.tag for t in meta_pool.tags.all()),
            "visible": meta_pool.visible,
            "policy": meta_pool.policy,
            "ha_policy": meta_pool.ha_policy,
            "transport_grouping": meta_pool.transport_grouping,
            "calendar_message": meta_pool.calendar_message,
        }
        snapshot: JsonObject = {}
        for name in wanted:
            if name in context:
                snapshot[name] = context[name]
            elif name in columns:
                snapshot[name] = columns[name]
        return snapshot

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        fields = self.etag_fields("metapool")
        item_dict: JsonObject = self.snapshot_values(target, fields)
        return item_etag(item_dict, fields)

    # ---------------------------------------------------------- execution

    @typing.override
    async def op_update(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # ALL the ORM work (target, FK context, tags) must stay out of the
        # async context: resolve + snapshot + merge in one sync boundary.
        def _build_params() -> tuple[str, JsonObject]:
            meta_pool = typing.cast(models.MetaPool, self.resolve_target(action.target_uuid))
            params = self.snapshot_values(meta_pool, self.etag_fields("metapool"))
            params.update(action.values)
            return meta_pool.name, params

        meta_pool_name, params = await sync_to_async(_build_params, thread_sensitive=True)()
        await RestProxy().execute(
            RestTarget(
                MetaPools,
                "meta_pools",
                types.rest.CustomMethodMethod.PUT,
                args=(action.target_uuid,),
            ),
            request,
            params,
        )
        return f'Meta pool "{meta_pool_name}" updated'


_MEMBER_ROW_KEYS: typing.Final[frozenset[str]] = frozenset({"pool_id", "priority", "enabled"})

MemberRow = dict[str, typing.Any]
MemberOperation = tuple[types.rest.CustomMethodMethod, str | None, MemberRow]


def _member_rows(meta_pool: models.MetaPool) -> list[MemberRow]:
    """Live members as canonical rows (sorted by pool uuid).

    The member row uuid is intentionally not part of the projection: the
    pool is the natural key of the relation, the row id is an execution
    detail resolved again at approve time.
    """
    return [
        {"pool_id": member.pool.uuid, "priority": member.priority, "enabled": member.enabled}
        for member in meta_pool.members.select_related("pool").order_by("pool__uuid")
    ]


class MetaPoolMembers(mutability_base.MutableActionType):
    """Proposal: set the complete desired member pool list of a meta pool.

    Relation action type (option A): ``members`` is never a delta — it
    describes the final desired set. Execution diffs it against the live
    rows (keyed by pool) and applies the minimal create/edit/delete
    sequence through the canonical ``meta_pools/{uuid}/pools`` surface.
    The exposed operations (set, plus get via the :meth:`read` override)
    are derived from the implemented hooks, not declared.
    """

    type_id = "metapool.members"

    title = "Propose meta pool members"
    description = (
        "Propose the complete desired set of member pools of a meta pool. The "
        "'members' field is the FINAL set, not a delta: pools missing from it "
        "will be removed, new ones added, and priority/enabled adjusted. Use "
        "get_metapool_members to see the current members and the uuids of the "
        "pools you can assign. An empty list removes all members. The proposal "
        "does NOT apply anything: it is queued until an administrator approves "
        "it."
    )
    handler = MetaPools
    model = models.MetaPool
    noun = "Meta pool"
    # Discovery without the meta pool uuid is useless here: the agent must
    # see the current set before it can propose the final one.
    target_scoped_fields = True

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.MetaPool.objects.get(uuid__iexact=target_uuid)
        except models.MetaPool.DoesNotExist:
            raise rest_exceptions.NotFound("Meta pool not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return "metapool"

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        pools: list[JsonObject] = [
            {"value": str(uuid), "label": name}
            for uuid, name in models.ServicePool.objects.order_by("name").values_list("uuid", "name")
        ]
        return [
            {
                "name": "members",
                "type": types.ui.FieldType.EDITABLELIST.value,
                "label": "Member pools",
                "tooltip": (
                    "Complete desired set of member pools. Each item is an object with pool_id "
                    "(uuid of an existing service pool), priority (integer >= 0, lower wins) and "
                    "enabled (boolean). Pools not listed are removed on approval."
                ),
                "secret": False,
                "row": {
                    "pool_id": "uuid of an existing service pool (choices)",
                    "priority": "integer >= 0",
                    "enabled": "boolean",
                },
                "choices": pools,
            }
        ]

    @typing.override
    def validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        errors = super().validate_values(for_type, values, target)
        flat = self.flatten_values(values)
        if "members" not in flat:
            errors.append("field members is required (the complete desired set; use [] to remove all)")
            return errors
        errors.extend(self._validate_rows(flat["members"]))
        if isinstance(target, models.MetaPool):
            live = [row["pool_id"] for row in _member_rows(target)]
            if len(set(live)) != len(live):
                errors.append(
                    "the meta pool currently has duplicated member rows in the database; "
                    "fix its membership through the admin UI before proposing again"
                )
        return errors

    @staticmethod
    def _validate_rows(rows: typing.Any) -> list[str]:
        if not isinstance(rows, (list, tuple)):
            return []  # base already flagged "must be a list"
        errors: list[str] = []
        seen: set[str] = set()
        rows_seq = typing.cast("collections.abc.Sequence[typing.Any]", rows)
        for index, row in enumerate(rows_seq):
            where = f"members[{index}]"
            if not isinstance(row, dict):
                errors.append(f"{where} must be an object with pool_id, priority and enabled")
                continue
            row_dict = typing.cast("dict[str, typing.Any]", row)
            unknown = sorted(set(row_dict) - _MEMBER_ROW_KEYS)
            missing = sorted(_MEMBER_ROW_KEYS - set(row_dict))
            if unknown:
                errors.append(
                    f"{where} has unknown keys: {', '.join(unknown)} (accepted: enabled, pool_id, priority)"
                )
            if missing:
                errors.append(f"{where} is missing keys: {', '.join(missing)}")
            pool_error, pool_id = _validate_pool_id(row_dict.get("pool_id"), where)
            if pool_error:
                errors.append(pool_error)
            elif typing.cast("str", pool_id) in seen:
                errors.append(f"{where} duplicates pool_id {pool_id} (a pool can appear only once)")
            else:
                seen.add(typing.cast("str", pool_id))
            errors.extend(_validate_priority(row_dict.get("priority"), where))
            if not isinstance(row_dict.get("enabled"), bool):
                errors.append(f"{where}.enabled must be a boolean")
        return errors

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        return ["members"]

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        meta_pool = typing.cast(models.MetaPool, target)
        wanted = set(names)
        return {"members": _member_rows(meta_pool)} if "members" in wanted else {}

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        rows = _member_rows(typing.cast(models.MetaPool, target))
        return hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()

    @typing.override
    def target_display_name(self, target: db_models.Model) -> str:
        return typing.cast(models.MetaPool, target).name

    # ------------------------------------------------------- tool text / read

    @typing.override
    def tool_title(self) -> str:
        if self.operation is mutability_base.ActionOperation.GET:
            return "Read meta pool members"
        return self.title

    @typing.override
    def tool_description(self) -> str:
        if self.operation is mutability_base.ActionOperation.GET:
            return (
                "Read the current member pools of one meta pool: every member with its pool uuid "
                "(to use in a metapool.members.set proposal), name, priority and enabled state. "
                "Applies immediately; it is a plain read, never a proposal."
            )
        return self.description

    @typing.override
    def read(self, target_uuid: str) -> JsonObject:
        result = super().read(target_uuid)
        meta_pool = typing.cast(models.MetaPool, self.resolve_target(target_uuid))
        names = {str(uuid): name for uuid, name in models.ServicePool.objects.values_list("uuid", "name")}
        result["current_members"] = [
            {**row, "pool_name": names.get(str(row["pool_id"]), "")} for row in _member_rows(meta_pool)
        ]
        return result

    # ---------------------------------------------------------- execution

    @typing.override
    async def op_set(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # Plan the diff in one sync boundary (target + live rows), then run
        # the REST operations. The whole-set CAS check (stale policy DENY)
        # already validated the live set against the approved base before
        # the executor got here.
        def _plan() -> tuple[str, list[MemberOperation]]:
            meta_pool = typing.cast(models.MetaPool, self.resolve_target(action.target_uuid))
            rows = _member_rows(meta_pool)
            current = {row["pool_id"]: row for row in rows}
            if len(current) != len(rows):
                raise rest_exceptions.RequestError(
                    f'Meta pool "{meta_pool.name}" has duplicated member rows; '
                    "fix its membership through the admin UI before proposing again"
                )
            # Member row uuids keyed by pool uuid (the row id itself is an
            # execution detail, resolved fresh at approve time, never stored)
            member_uuids = {
                member.pool.uuid: member.uuid for member in meta_pool.members.select_related("pool")
            }
            desired: dict[str, MemberRow] = {
                process_uuid(typing.cast(str, row["pool_id"])): {
                    "pool_id": process_uuid(typing.cast(str, row["pool_id"])),
                    "priority": typing.cast(int, row["priority"]),
                    "enabled": typing.cast(bool, row["enabled"]),
                }
                for row in typing.cast("list[MemberRow]", action.values["members"])
            }
            operations: list[MemberOperation] = [
                (types.rest.CustomMethodMethod.DELETE, member_uuids[pool_id], {})
                for pool_id in sorted(set(current) - set(desired))
            ]
            operations += [
                (types.rest.CustomMethodMethod.POST, None, desired[pool_id])
                for pool_id in sorted(set(desired) - set(current))
            ]
            # pool_id is equal by construction, so the row dicts compare on
            # priority/enabled only
            operations += [
                (types.rest.CustomMethodMethod.PUT, member_uuids[pool_id], desired[pool_id])
                for pool_id in sorted(set(desired) & set(current))
                if current[pool_id] != desired[pool_id]
            ]
            return meta_pool.name, operations

        meta_pool_name, operations = await sync_to_async(_plan, thread_sensitive=True)()

        proxy = RestProxy()
        parent = RestTarget(MetaPools, "meta_pools")
        for method, member_uuid, params in operations:
            await proxy.execute(
                RestTarget(
                    MetaServicesPool,
                    "meta_pools/{uuid}/pools",
                    method,
                    args=(member_uuid,) if member_uuid is not None else (),
                    parent=parent,
                ),
                request,
                dict(params),
                parent_uuid=action.target_uuid,
            )

        created = sum(1 for method, _, _ in operations if method == types.rest.CustomMethodMethod.POST)
        modified = sum(1 for method, _, _ in operations if method == types.rest.CustomMethodMethod.PUT)
        removed = sum(1 for method, _, _ in operations if method == types.rest.CustomMethodMethod.DELETE)
        return (
            f'Meta pool "{meta_pool_name}" members: {created} created, {modified} modified, {removed} removed'
        )


def _validate_pool_id(value: typing.Any, where: str) -> tuple[str | None, str | None]:
    """Validate one row pool_id; returns (error, normalized uuid or None)."""
    if not isinstance(value, str):
        return (f"{where}.pool_id must be the uuid string of an existing service pool", None)
    try:
        pool_id = process_uuid(value)
    except ValueError:
        return (f"{where}.pool_id is not a valid uuid: {value!r}", None)
    if not models.ServicePool.objects.filter(uuid=pool_id).exists():
        return (f"{where}.pool_id does not match any existing service pool: {pool_id}", None)
    return (None, pool_id)


def _validate_priority(value: typing.Any, where: str) -> list[str]:
    if isinstance(value, bool) or not isinstance(value, int):
        return [f"{where}.priority must be an integer >= 0"]
    if value < 0:
        return [f"{where}.priority must be >= 0"]
    return []


class MetaPoolGroups(M2MRelationActionType):
    """Proposal: set the complete desired access groups of a meta pool."""

    type_id = "metapool.groups"
    relation_label = "access groups"
    uuid_source = "get_metapool_groups (or the group list tools)"
    title = "Propose meta pool groups"
    description = (
        "Propose the complete desired set of groups allowed to use a meta pool. The "
        "'groups' field is the FINAL set, not a delta: groups missing from it lose "
        "access, new ones gain it. Group uuids come from the groups list tools "
        "(authenticator groups are also assignable). An empty list removes access "
        "for every group. The proposal does NOT apply anything: it is queued until "
        "an administrator approves it."
    )
    handler = MetaPools
    model = models.MetaPool
    noun = "Meta pool"
    subtype = "metapool"

    field_name = "groups"
    field_label = "Allowed groups"
    field_tooltip = (
        "Complete desired set of group uuids allowed to use this meta pool. Pairs are "
        "validated against existing groups; discover the uuids with the groups tools."
    )
    item_label = "group"
    related_model = models.Group
    include_choices = False
    relation_manager = "assignedGroups"
    detail_handler = AssignedGroups
    detail_path = "meta_pools/{uuid}/groups"
    parent_collection = "meta_pools"
