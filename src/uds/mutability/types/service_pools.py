"""``servicepool.update``: propose modifications to an existing service pool.

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
from uds.REST.methods.services_pools import ServicesPools

from .. import base as mutability_base
from ..etag import item_etag

JsonObject = dict[str, typing.Any]


class ServicePoolUpdate(mutability_base.MutableActionType):
    """Proposal: update an existing service pool."""

    type_id = "servicepool.update"
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
        return [
            {
                "name": "name",
                "type": "text",
                "label": "Name",
                "tooltip": "Name of the service pool",
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
                "tooltip": "Comments of the service pool",
                "secret": False,
            },
            {
                "name": "tags",
                "type": "taglist",
                "label": "Tags",
                "tooltip": "Tags of the pool (list)",
                "secret": False,
            },
            {
                "name": "initial_srvs",
                "type": "numeric",
                "label": "Initial available services",
                "tooltip": "Services created initially to speed up first assignment",
                "secret": False,
            },
            {
                "name": "cache_l1_srvs",
                "type": "numeric",
                "label": "Services to keep in cache",
                "tooltip": "Services kept ready for immediate use",
                "secret": False,
            },
            {
                "name": "cache_l2_srvs",
                "type": "numeric",
                "label": "Services to keep in L2 cache",
                "tooltip": "Services kept in a suspended-like state",
                "secret": False,
            },
            {
                "name": "max_srvs",
                "type": "numeric",
                "label": "Maximum services",
                "tooltip": "Maximum number of user services (capped below by cache values on apply)",
                "secret": False,
            },
            {
                "name": "show_transports",
                "type": "checkbox",
                "label": "Shows transports",
                "tooltip": "Show transports to users",
                "secret": False,
            },
            {
                "name": "visible",
                "type": "checkbox",
                "label": "Visible",
                "tooltip": "Pool visible to users",
                "secret": False,
            },
            {
                "name": "allow_users_remove",
                "type": "checkbox",
                "label": "Allow removal",
                "tooltip": "Users may remove their assigned services",
                "secret": False,
            },
            {
                "name": "allow_users_reset",
                "type": "checkbox",
                "label": "Allow reset",
                "tooltip": "Users may reset their assigned services",
                "secret": False,
            },
            {
                "name": "ignores_unused",
                "type": "checkbox",
                "label": "Ignores unused",
                "tooltip": "Ignored by the unused service cleanup",
                "secret": False,
            },
            {
                "name": "calendar_message",
                "type": "text",
                "label": "Calendar access denied text",
                "tooltip": "Message shown when access is denied by calendar",
                "secret": False,
            },
            {
                "name": "custom_message",
                "type": "text",
                "label": "Custom launch message text",
                "tooltip": "Message shown when the service is launched",
                "secret": False,
            },
            {
                "name": "display_custom_message",
                "type": "checkbox",
                "label": "Enable custom launch message",
                "tooltip": "Whether the custom launch message is displayed",
                "secret": False,
            },
        ]

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

    # ---------------------------------------------------------- execution

    @typing.override
    async def execute(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
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
