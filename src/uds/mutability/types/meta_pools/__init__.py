"""``metapool``: propose to create, modify, or delete a meta pool.

Top level resource with a static gui (no module instance). Since the
gui-coupling trial, ``metapool.update`` derives its mutable surface from
``MetaPools.get_gui`` itself (the same definitions the admin form uses):
fields are proposable by default, FK references travelling on the
form-shaped PUT are annotated ``context`` on the gui and therefore join
the fingerprint but never the proposable surface. GUI changes propagate
to the agent automatically.

``create`` renders the creation view of the same gui, where the ``context``
references (image and pool group) join the surface too: on creation they
are selected, not changed, exactly like the administration form. A meta
pool is created empty — its member pools are a relation with its own
verb — so the creation carries only the form fields.

``delete`` takes no fields: the meta pool itself is the target. Unlike
service pools (which are only marked REMOVABLE and cleaned up
asynchronously), the REST DELETE removes the meta pool row right away,
so it keeps the strict whole-item CAS: any change after the proposal
(snapshot of the gui fields) denies it, no matter who made it.

The other meta pool families live in sibling modules, one per action
type (``access``, ``assignment``, ``fallback``, ``group``,
``members``); the imports at the bottom of this module pull them in so
the registry discovers every family of the package.
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
from ... import verbs as mutability_verbs
from ...base import ActionOperation, StalePolicy
from ...etag import item_etag

from . import access as access
from . import assignment as assignment
from . import fallback as fallback
from . import group as group
from . import members as members

JsonObject = dict[str, typing.Any]


def _metapool_gui_elements() -> list[types.ui.GuiElement]:
    """The admin gui of meta pools, ordered as the builder declared it."""
    shim = typing.cast(typing.Any, _Namespace())
    return sorted(MetaPools.get_gui(shim, "metapool"), key=lambda element: element.gui.order)


class MetaPoolUpdate(mutability_base.MutableActionType):
    """Proposal: update or delete an existing meta pool."""

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
    # A meta pool is not a module: no subtype gallery, no data_type
    create_has_gallery = False
    # Both operations are synchronous writes gated by the strict whole-item
    # CAS: the delete removes the row right away (no REMOVABLE intermediate
    # state like service pools), so any change after the snapshot denies it.
    stale_policy = StalePolicy.DENY

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
        # Deletion needs no fields: the target itself is what disappears.
        # (An unbound instance serves the update surface, its default op.)
        if self.operation is ActionOperation.DELETE:
            return []
        if self.operation is ActionOperation.CREATE:
            # Defensive: the create machinery routes through
            # create_field_definitions; a create-bound instance asked for
            # the plain surface must answer with the creation view.
            return self.create_field_definitions(for_type, target)
        # Agent view of the admin gui: only proposable fields are part of
        # the mutable surface (context references and relations stay out,
        # though context still travels in the fingerprint / PUT payload).
        return gui_view.agent_definitions(_metapool_gui_elements())

    @typing.override
    def create_field_definitions(
        self,
        for_type: str,
        target: db_models.Model | None = None,
    ) -> list[JsonObject]:
        # The creation view of the same gui: the context references (image,
        # pool group) join the surface — selected, not changed.
        return gui_view.agent_definitions(_metapool_gui_elements(), for_creation=True)

    @typing.override
    def create_validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        # Meta pools have no subtype gallery: the data_type is not required.
        # The base validation already checks the choices universes (policies,
        # image, pool group); the only thing it cannot enforce is that the
        # name actually carries text.
        errors = super().create_validate_values(for_type or "metapool", values, target)
        if not str(values.get("name", "")).strip():
            errors.append("field name: required")
        return errors

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
    def tool_title(self) -> str:
        if self.operation is ActionOperation.CREATE:
            return mutability_verbs.create_tool_title("meta pool")
        if self.operation is ActionOperation.DELETE:
            return "Propose deleting a meta pool"
        return self.title

    @typing.override
    def tool_description(self) -> str:
        if self.operation is ActionOperation.CREATE:
            return mutability_verbs.create_tool_description(
                "meta pool",
                "name (required), short_name, comments, tags, visible, policy "
                "(load balancing), ha_policy, transport_grouping, calendar_message, "
                "image_id and pool_group_id (the image and pool group pickers)",
                extra=(
                    "The new meta pool is created empty: its member service pools "
                    "are attached through the metapool members verbs, not here."
                ),
            )
        if self.operation is ActionOperation.DELETE:
            return (
                "Propose deleting a meta pool: the grouping disappears for good — its "
                "member service pools are NOT deleted, they simply stop being part of "
                "this meta pool. No fields are needed: the meta pool itself is the "
                "target of the proposal. The proposal does NOT delete anything: it is "
                "queued until an administrator approves it, and it is refused if the "
                "meta pool changed since it was proposed."
            )
        return self.description

    @typing.override
    async def op_create(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        values = dict(action.values)
        # The POST is form-shaped: every required key of FIELDS_TO_SAVE
        # rides, omitted ones take the value the admin form sends on "new"
        # ("" for texts, the gui default for pickers/checkboxes, "-1" for
        # the image/pool-group sentinels). The meta pool starts empty: no
        # members travel with the creation.
        params: JsonObject = {
            "name": values.get("name"),
            "short_name": values.get("short_name", ""),
            "comments": values.get("comments", ""),
            "tags": values.get("tags", []),
            "image_id": values.get("image_id", "-1"),
            "servicesPoolGroup_id": values.get("servicesPoolGroup_id", "-1"),
            "visible": values.get("visible", True),
            "policy": values.get("policy", int(types.pools.LoadBalancingPolicy.ROUND_ROBIN)),
            "ha_policy": values.get("ha_policy", int(types.pools.HighAvailabilityPolicy.DISABLED)),
            "calendar_message": values.get("calendar_message", ""),
            "transport_grouping": values.get(
                "transport_grouping", int(types.pools.TransportSelectionPolicy.AUTO)
            ),
        }
        new_uuid = await mutability_verbs.execute_create(
            RestTarget(MetaPools, "meta_pools", types.rest.CustomMethodMethod.POST),
            request,
            params,
        )
        return mutability_verbs.created_message("Meta pool", str(params["name"]), new_uuid)

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

    @typing.override
    async def op_delete(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The REST DELETE removes the row synchronously (member service
        # pools survive; only the grouping goes away).
        meta_pool = typing.cast(
            models.MetaPool,
            await sync_to_async(self.resolve_target, thread_sensitive=True)(action.target_uuid),
        )
        await RestProxy().execute(
            RestTarget(
                MetaPools,
                "meta_pools",
                types.rest.CustomMethodMethod.DELETE,
                args=(action.target_uuid,),
            ),
            request,
            {},
        )
        return f'Meta pool "{meta_pool.name}" deleted'
