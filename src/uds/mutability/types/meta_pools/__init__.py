"""``metapool.update``: propose modifications to an existing meta pool.

Top level resource with a static gui (no module instance). Since the
gui-coupling trial, ``metapool.update`` derives its mutable surface from
``MetaPools.get_gui`` itself (the same definitions the admin form uses):
fields are proposable by default, FK references travelling on the
form-shaped PUT are annotated ``context`` on the gui and therefore join
the fingerprint but never the proposable surface. GUI changes propagate
to the agent automatically.

The other meta pool families live in sibling modules, one per action
type (``members``, ``group``); the imports at the bottom of this module
pull them in so the registry discovers every family of the package.
"""

import collections.abc
import typing
from types import SimpleNamespace as _Namespace

from asgiref.sync import sync_to_async
from django.db import models as db_models

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.mcp.rest_proxy import RestProxy, RestTarget
from uds.REST.methods.meta_pools import MetaPools

from ... import base as mutability_base
from ... import gui_view
from ...etag import item_etag

from . import group as group
from . import members as members

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
