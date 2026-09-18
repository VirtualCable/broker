"""``group.update``: propose modifications to an existing group.

Second level resource (authenticators detail): the proposal only needs
the group uuid — the parent authenticator is derived from the ``manager``
FK at execution time (a group never changes authenticator), exactly like
``calendar_rule.update`` does for rules.

The groups PUT is form-shaped and requires ``name``, ``comments``,
``state``, ``skip_mfa`` plus a ``type`` marker; ``meta_if_any`` is
optional on the wire but it is applied unconditionally, so it is always
sent. On update the handler ignores ``name`` (cannot be renamed) and the
``type`` (a group cannot be converted normal<->meta), so both travel as
context in every payload but are not proposable.

The ``groups`` (m2m members of a meta group) and ``pools`` (m2m pool
grants) relations are NOT part of the mutable surface.
"""

import collections.abc
import typing

from asgiref.sync import sync_to_async
from django.db import models as db_models

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.mcp.rest_proxy import RestProxy, RestTarget
from uds.REST.methods.authenticators import Authenticators
from uds.REST.methods.users_groups import Groups

from ... import base as mutability_base
from ... import verbs as mutability_verbs
from ...etag import item_etag

JsonObject = dict[str, typing.Any]

# Snapshot keys: mutable columns plus the immutable context the PUT
# requires (name, type) but the proposal cannot touch
_GROUP_FIELDS: typing.Final[list[str]] = [
    "name",
    "type",
    "comments",
    "state",
    "skip_mfa",
    "meta_if_any",
]


class GroupUpdate(mutability_base.MutableActionType):
    """Proposal: update an existing group."""

    type_id = "group"
    title = "Propose group update"
    description = (
        "Propose changes to an existing group of an authenticator. The proposal does "
        "NOT apply anything: it is queued until an administrator approves it. Mutable "
        "fields are comments, state, skip_mfa and, for meta groups, meta_if_any. The "
        "group name and its kind (normal/meta) cannot be changed, and membership "
        "(users of a meta group) or pool grants are not part of this proposal."
    )
    model = models.Group
    noun = "Group"
    # A creation is a detail of the parent authenticator: the proposal
    # targets the authenticator uuid and MANAGEMENT over it is the
    # create permission.
    create_needs_parent = True

    @typing.override
    def permission_target(self, target: db_models.Model) -> db_models.Model:
        # A group is a detail of its authenticator: MANAGEMENT is held over
        # the parent, exactly like the REST dispatcher checks it.
        return typing.cast(models.Group, target).manager

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.Group.objects.get(uuid__iexact=target_uuid)
        except models.Group.DoesNotExist:
            raise rest_exceptions.NotFound("Group not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return "group"

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        return [
            {
                "name": "comments",
                "type": "text",
                "label": "Comments",
                "tooltip": "Comments of the group",
                "secret": False,
            },
            {
                "name": "state",
                "type": "text",
                "label": "State",
                "tooltip": "One of: A (active), I (inactive), B (blocked)",
                "secret": False,
            },
            {
                "name": "skip_mfa",
                "type": "text",
                "label": "Skip MFA",
                "tooltip": "Whether MFA validation is skipped for this group",
                "secret": False,
            },
            {
                "name": "meta_if_any",
                "type": "checkbox",
                "label": "Meta if any",
                "tooltip": "Meta group matches when the user belongs to ANY member "
                "group instead of ALL of them (meta groups only)",
                "secret": False,
            },
        ]

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        group = typing.cast(models.Group, target)
        wanted = set(names)
        snapshot: JsonObject = {}
        for name in wanted:
            if name == "name":
                snapshot[name] = group.name
            elif name == "type":
                # Same marker the REST PUT expects in params
                snapshot[name] = "meta" if group.is_meta else "normal"
            elif name == "comments":
                snapshot[name] = group.comments
            elif name == "state":
                snapshot[name] = group.state
            elif name == "skip_mfa":
                snapshot[name] = group.skip_mfa
            elif name == "meta_if_any":
                snapshot[name] = group.meta_if_any
        return snapshot

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        return list(_GROUP_FIELDS)

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        fields = self.etag_fields("group")
        item_dict: JsonObject = self.snapshot_values(target, fields)
        return item_etag(item_dict, fields)

    # ---------------------------------------------------------- execution

    @typing.override
    def tool_title(self) -> str:
        if self.operation is mutability_base.ActionOperation.CREATE:
            return mutability_verbs.create_tool_title("group")
        if self.operation is mutability_base.ActionOperation.DELETE:
            return mutability_verbs.delete_tool_title("group")
        return self.title

    @typing.override
    def tool_description(self) -> str:
        if self.operation is mutability_base.ActionOperation.CREATE:
            return mutability_verbs.create_tool_description(
                "group",
                "name, comments, state, skip_mfa and, for meta groups, is_meta plus "
                "the member group uuids (groups, comma separated). Pool grants are "
                "not part of the creation.",
                needs_parent=True,
            )
        if self.operation is mutability_base.ActionOperation.DELETE:
            return mutability_verbs.delete_tool_description(
                "group of an authenticator",
                "the group is removed (users keep their other groups), like the administration interface does.",
            )
        return self.description

    # ---------------------------------------------------- creation hooks

    @typing.override
    def resolve_create_parent(self, parent_uuid: str) -> db_models.Model:
        # The container of a group creation is its authenticator
        try:
            return models.Authenticator.objects.get(uuid__iexact=parent_uuid)
        except models.Authenticator.DoesNotExist:
            raise rest_exceptions.NotFound("Authenticator not found") from None

    @typing.override
    def create_field_definitions(
        self, for_type: str, target: db_models.Model | None = None
    ) -> list[JsonObject]:
        # The creation form adds the group name and the kind (normal vs
        # meta, with its member groups) on top of the update surface
        return [
            {
                "name": "name",
                "type": "text",
                "label": "Group name",
                "tooltip": "Name of the group (prefix it with pat: to declare a pattern group)",
                "secret": False,
            },
            *self.field_definitions(for_type, None),
            {
                "name": "is_meta",
                "type": "checkbox",
                "label": "Meta group",
                "tooltip": "Meta groups hold no direct members: they match users through their member groups",
                "secret": False,
            },
            {
                "name": "groups",
                "type": "text",
                "label": "Member groups",
                "tooltip": "Comma separated uuids of the member groups (meta groups only)",
                "secret": False,
            },
        ]

    @typing.override
    def create_validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        # Groups have no subtype gallery: the data_type is not required
        return super().create_validate_values(for_type or "group", values, target)

    @typing.override
    async def op_create(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The groups POST is form-shaped and requires every basic column
        # plus the type marker: safe defaults for the ones a proposal
        # omits. ALL the ORM work stays out of the async context.
        def _build_params() -> tuple[str, str, JsonObject]:
            authenticator = typing.cast(models.Authenticator, self.resolve_create_parent(action.target_uuid))
            values = dict(action.values)
            is_meta = bool(values.get("is_meta", False))
            params: JsonObject = {
                "name": values.get("name"),
                "comments": values.get("comments", ""),
                "state": values.get("state", "A"),
                "skip_mfa": values.get("skip_mfa", False),
                "meta_if_any": values.get("meta_if_any", False),
                "type": "meta" if is_meta else "normal",
            }
            if is_meta:
                raw_groups = str(values.get("groups", "") or "")
                params["groups"] = [g.strip() for g in raw_groups.split(",") if g.strip()]
            return str(params["name"]), authenticator.uuid, params

        name, authenticator_uuid, params = await sync_to_async(_build_params, thread_sensitive=True)()
        new_uuid = await mutability_verbs.execute_create(
            RestTarget(
                Groups,
                "authenticators/{uuid}/groups",
                types.rest.CustomMethodMethod.POST,
                parent=RestTarget(Authenticators, "authenticators"),
            ),
            request,
            params,
            authenticator_uuid,
        )
        return mutability_verbs.created_message("Group", name, new_uuid)

    @typing.override
    async def op_delete(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The REST DELETE removes the group row. ALL the ORM work stays
        # out of the async context, including the parent lookup.
        def _resolve() -> tuple[str, str]:
            group = typing.cast(models.Group, self.resolve_target(action.target_uuid))
            return group.name, group.manager.uuid

        name, authenticator_uuid = await sync_to_async(_resolve, thread_sensitive=True)()
        await mutability_verbs.execute_delete(
            RestTarget(
                Groups,
                "authenticators/{uuid}/groups",
                types.rest.CustomMethodMethod.DELETE,
                args=(action.target_uuid,),
                parent=RestTarget(Authenticators, "authenticators"),
            ),
            request,
            authenticator_uuid,
        )
        return f'Group "{name}" deleted'

    @typing.override
    async def op_update(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # ORM work must stay out of the async context, including the
        # parent lookup (the authenticator FK needs a query)
        def _resolve() -> tuple[models.Group, str]:
            group = typing.cast(models.Group, self.resolve_target(action.target_uuid))
            return group, group.manager.uuid

        group, authenticator_uuid = await sync_to_async(_resolve, thread_sensitive=True)()
        # The groups PUT is form-shaped (all listed fields required plus
        # the type marker): merge the proposal over the CAS-verified
        # current values.
        params: JsonObject = self.snapshot_values(group, self.etag_fields("group"))
        params.update(action.values)
        target = RestTarget(
            Groups,
            "authenticators/{uuid}/groups",
            types.rest.CustomMethodMethod.PUT,
            args=(action.target_uuid,),
            parent=RestTarget(Authenticators, "authenticators"),
        )
        # Detail targets need the parent uuid to resolve (and permission
        # check) the authenticator, so the sync boundary is invoked directly.
        await sync_to_async(RestProxy._execute_sync, thread_sensitive=True)(
            target, request, params, authenticator_uuid
        )
        return f'Group "{group.name}" updated'
