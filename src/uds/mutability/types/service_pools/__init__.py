"""``servicepool``: propose modifications to, or deletion of, a service pool.

Top level resource with a static gui (no module instance): the mutable
surface is model columns only. Publications, assignments and calendar
associations are *operations*, never fields. The base service, OS
manager, image, pool group and account are identity/reference columns
and are NOT proposable — but they travel as immutable context in every
payload, because the pool PUT is form-shaped and requires them (and
``pre_save`` resolves the base service from its uuid).

The readonly gui fields (``service_id``, ``osmanager_id``,
``publish_on_save``, ``account_id``) are additionally enforced by the
REST layer on update: only the stored value passes.

``delete`` takes no fields: the pool itself is the target. It only
marks the pool REMOVABLE — the background cleaners then take its
publications, cached and assigned user services away — so it runs under
FORCE like the other asynchronous verbs of the package, while the
form-shaped ``update`` keeps the strict DENY CAS.

The other service pool families live in sibling modules, one per
action type (``access``, ``action``, ``assignment``, ``cached``,
``fallback``, ``group``, ``publication``, ``transport``); the imports
at the bottom of this module pull them in so the registry discovers
every family of the package.
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
from uds.REST.methods.services_pools import ServicesPools

from ... import base as mutability_base
from ... import gui_view
from ...base import ActionOperation, StalePolicy
from ...etag import item_etag

from . import access as access
from . import action as action
from . import assignment as assignment
from . import cached as cached
from . import fallback as fallback
from . import group as group
from . import publication as publication
from . import transport as transport

JsonObject = dict[str, typing.Any]


class ServicePoolUpdate(mutability_base.MutableActionType):
    """Proposal: update or delete an existing service pool."""

    type_id = "servicepool"
    title = "Propose service pool update"
    description = (
        "Propose changes to an existing service pool (a service made available to "
        "users, with its cache and permissions). The proposal does NOT apply "
        "anything: it is queued until an administrator approves it. Mutable fields "
        "are name, short_name, comments, tags, cache sizes (initial_srvs, "
        "cache_l1_srvs, cache_l2_srvs, max_srvs), visibility and user permissions "
        "(visible, show_transports, allow_users_remove, allow_users_reset, "
        "ignores_unused) and messages (calendar_message, custom_message, "
        "display_custom_message). The base service, OS manager, image, pool group "
        "and account cannot be changed, and publications/assignments are "
        "operations outside this proposal."
    )
    handler = ServicesPools
    model = models.ServicePool
    noun = "Service pool"
    # update is a form-shaped synchronous write (strict whole-item CAS);
    # delete only marks the pool REMOVABLE — the cleaners do the actual
    # removal asynchronously — so it rides FORCE, like the asynchronous
    # verbs of the sibling families.
    stale_policy = StalePolicy.DENY
    stale_policies_overrides: typing.ClassVar[dict[ActionOperation, StalePolicy]] = {
        ActionOperation.DELETE: StalePolicy.FORCE
    }

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.ServicePool.objects.get(uuid__iexact=target_uuid)
        except models.ServicePool.DoesNotExist:
            raise rest_exceptions.NotFound("Service pool not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return "servicepool"

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        # Deletion needs no fields: the target itself is what disappears.
        # (An unbound instance serves the update surface, its default op.)
        if self.operation is ActionOperation.DELETE:
            return []
        # Agent view derived from the handler gui. What the gui marks
        # readonly (base service, os manager, account) or hides through the
        # mutability overlay (image, pool group, publish_on_save) is not
        # proposable: those are reference columns, and publishing is an
        # operation, not a field
        columns = frozenset(
            name for name, _modifier in ServicesPools.parse_save_fields(ServicesPools.FIELDS_TO_SAVE)
        )
        return gui_view.agent_definitions(
            self._gui_elements(for_type),
            from_instance=lambda name: name not in columns,
        )

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        # Whole-item fingerprint: the mutable columns plus the immutable
        # context the PUT requires (identity/reference columns)
        return [
            "name",
            "short_name",
            "comments",
            "tags",
            "service_id",
            "osmanager_id",
            "image_id",
            "pool_group_id",
            "account_id",
            "initial_srvs",
            "cache_l1_srvs",
            "cache_l2_srvs",
            "max_srvs",
            "show_transports",
            "visible",
            "allow_users_remove",
            "allow_users_reset",
            "ignores_unused",
            "calendar_message",
            "custom_message",
            "display_custom_message",
        ]

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        pool = typing.cast(models.ServicePool, target)
        wanted = set(names)
        # Immutable context: uuids as the PUT expects them ("" or "-1"
        # meaning none, same convention the admin UI uses)
        context: JsonObject = {
            "service_id": pool.service.uuid,
            "osmanager_id": pool.osmanager.uuid if pool.osmanager else "",
            "image_id": pool.image.uuid if pool.image else "-1",
            "pool_group_id": pool.servicesPoolGroup.uuid if pool.servicesPoolGroup else "-1",
            "account_id": pool.account.uuid if pool.account else "",
        }
        columns: JsonObject = {
            "name": pool.name,
            "short_name": pool.short_name,
            "comments": pool.comments,
            "tags": sorted(t.tag for t in pool.tags.all()),
            "initial_srvs": pool.initial_srvs,
            "cache_l1_srvs": pool.cache_l1_srvs,
            "cache_l2_srvs": pool.cache_l2_srvs,
            "max_srvs": pool.max_srvs,
            "show_transports": pool.show_transports,
            "visible": pool.visible,
            "allow_users_remove": pool.allow_users_remove,
            "allow_users_reset": pool.allow_users_reset,
            "ignores_unused": pool.ignores_unused,
            "calendar_message": pool.calendar_message,
            "custom_message": pool.custom_message,
            "display_custom_message": pool.display_custom_message,
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
        fields = self.etag_fields("servicepool")
        item_dict: JsonObject = self.snapshot_values(target, fields)
        return item_etag(item_dict, fields)

    # ---------------------------------------------------------- helpers

    def _gui_elements(self, for_type: str) -> list[types.ui.GuiElement]:
        """The handler gui, ordered as the builder declared.

        ``ServicesPools.get_gui`` is request-independent; binding it to an
        empty namespace avoids instantiating the full handler (whose
        ``__init__`` resolves authentication).
        """
        shim = typing.cast(typing.Any, _Namespace())
        return sorted(ServicesPools.get_gui(shim, for_type), key=lambda element: element.gui.order)

    # ---------------------------------------------------------- execution

    @typing.override
    def tool_title(self) -> str:
        if self.operation is ActionOperation.DELETE:
            return "Propose deleting a service pool"
        return self.title

    @typing.override
    def tool_description(self) -> str:
        if self.operation is ActionOperation.DELETE:
            return (
                "Propose deleting a service pool: it is marked for removal and the "
                "background cleaners then take its publications, cached and assigned "
                "user services away; the pool disappears for good once everything is "
                "gone. No fields are needed: the pool itself is the target of the "
                "proposal. The proposal does NOT delete anything: it is queued until "
                "an administrator approves it."
            )
        return self.description

    @typing.override
    async def op_update(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # ALL the ORM work (target, FK context, tags) must stay out of the
        # async context: resolve + snapshot + merge in one sync boundary.
        def _build_params() -> tuple[str, JsonObject]:
            pool = typing.cast(models.ServicePool, self.resolve_target(action.target_uuid))
            params = self.snapshot_values(pool, self.etag_fields("servicepool"))
            params.update(action.values)
            return pool.name, params

        pool_name, params = await sync_to_async(_build_params, thread_sensitive=True)()
        await RestProxy().execute(
            RestTarget(
                ServicesPools,
                "services_pools",
                types.rest.CustomMethodMethod.PUT,
                args=(action.target_uuid,),
            ),
            request,
            params,
        )
        return f'Service pool "{pool_name}" updated'

    @typing.override
    async def op_delete(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # REST only marks the pool REMOVABLE; the cleaners perform the real
        # removal asynchronously (publications, caches and user services
        # first, the pool row last).
        pool = typing.cast(
            models.ServicePool,
            await sync_to_async(self.resolve_target, thread_sensitive=True)(action.target_uuid),
        )
        await RestProxy().execute(
            RestTarget(
                ServicesPools,
                "services_pools",
                types.rest.CustomMethodMethod.DELETE,
                args=(action.target_uuid,),
            ),
            request,
            {},
        )
        return f'Service pool "{pool.name}" queued for removal'
