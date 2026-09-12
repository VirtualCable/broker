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

from .. import base as mutability_base
from ..etag import item_etag

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

    type_id = "group.update"
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
    async def execute(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
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
