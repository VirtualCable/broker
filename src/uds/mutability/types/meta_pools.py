"""``metapool.update``: propose modifications to an existing meta pool.

Top level resource with a static gui (no module instance): the mutable
surface is model columns only. Pool membership (the ``members`` m2m)
and user/calendar associations are relations outside this proposal.
The image and service pool group are reference columns and are NOT
proposable, but they travel as immutable context in every payload
(the meta pool PUT is form-shaped and requires them).
"""

import collections.abc
import typing
from asgiref.sync import sync_to_async
from django.db import models as db_models

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.pools import HighAvailabilityPolicy, LoadBalancingPolicy, TransportSelectionPolicy
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.mcp.rest_proxy import RestProxy, RestTarget
from uds.REST.methods.meta_pools import MetaPools

from .. import base as mutability_base
from ..etag import item_etag

JsonObject = dict[str, typing.Any]


def _def_from_choice(
    name: str,
    label: str,
    tooltip: str,
    choices: collections.abc.Iterable[tuple[int, str]],
) -> JsonObject:
    return {
        "name": name,
        "type": "choice",
        "label": label,
        "tooltip": tooltip,
        "secret": False,
        "choices": [value for value, _title in choices],
    }


class MetaPoolUpdate(mutability_base.MutableActionType):
    """Proposal: update an existing meta pool."""

    type_id = "metapool.update"
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
        return [
            {
                "name": "name",
                "type": "text",
                "label": "Name",
                "tooltip": "Name of the meta pool",
                "secret": False,
            },
            {
                "name": "short_name",
                "type": "text",
                "label": "Short name",
                "tooltip": "Short name for user service visualization",
                "secret": False,
            },
            {
                "name": "comments",
                "type": "text",
                "label": "Comments",
                "tooltip": "Comments of the meta pool",
                "secret": False,
            },
            {
                "name": "tags",
                "type": "taglist",
                "label": "Tags",
                "tooltip": "Tags of the meta pool (list)",
                "secret": False,
            },
            {
                "name": "visible",
                "type": "checkbox",
                "label": "Visible",
                "tooltip": "Meta pool visible to users",
                "secret": False,
            },
            _def_from_choice(
                "policy",
                "Selection policy",
                "How the pool is selected for each user",
                LoadBalancingPolicy.enumerate(),
            ),
            _def_from_choice(
                "ha_policy",
                "High availability",
                "Behaviour when a member pool fails",
                HighAvailabilityPolicy.enumerate(),
            ),
            _def_from_choice(
                "transport_grouping",
                "Transport grouping",
                "How transports are shown to the user",
                TransportSelectionPolicy.enumerate(),
            ),
            {
                "name": "calendar_message",
                "type": "text",
                "label": "Calendar access denied text",
                "tooltip": "Message shown when access is denied by calendar",
                "secret": False,
            },
        ]

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        # Whole-item fingerprint: mutable columns plus the immutable
        # reference context the PUT requires
        return [
            "name",
            "short_name",
            "comments",
            "tags",
            "image_id",
            "servicesPoolGroup_id",
            "visible",
            "policy",
            "ha_policy",
            "calendar_message",
            "transport_grouping",
        ]

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
    async def execute(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
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
