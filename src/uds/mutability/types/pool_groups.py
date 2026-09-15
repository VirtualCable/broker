"""``service_pool_group.update``: propose modifications to an existing
service pool group (the "Pool Groups" shown to users).

Replicates the REST ``PUT /service-pool-groups/{uuid}``: ``name``,
``comments`` and ``priority``. The image is a foreign key to an uploaded
binary, so the handler hides it through the mutability overlay and it is
NOT offered as mutable.

Validation, serialization and execution reuse the very same handler
machinery.
"""

import collections.abc
import typing
from types import SimpleNamespace as _Namespace

from asgiref.sync import sync_to_async
from django.db import models as db_models

from uds import models
from uds.core import types
from uds.REST.methods.services_pool_groups import ServicesPoolGroups
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.mcp.rest_proxy import RestProxy, RestTarget

from .. import base as mutability_base
from .. import gui_view
from ..etag import item_etag

JsonObject = dict[str, typing.Any]


class ServicePoolGroupUpdate(mutability_base.MutableActionType):
    """Proposal: update an existing service pool group."""

    type_id = "service_pool_group"
    title = "Propose service pool group update"
    description = (
        "Propose changes to an existing service pool group (a folder of service pools "
        "shown to users). The proposal does NOT apply anything: it is queued until an "
        "administrator approves it. Mutable fields are name, comments and priority; "
        "use get_mutable_fields to discover them and their current values."
    )
    handler = ServicesPoolGroups

    # ------------------------------------------------------------- hooks

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.ServicePoolGroup.objects.get(uuid__iexact=target_uuid)
        except models.ServicePoolGroup.DoesNotExist:
            raise rest_exceptions.NotFound("Service pool group not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return "service_pool_group"

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        shim = typing.cast(typing.Any, _Namespace())
        # The handler hides the image fk (an uploaded binary id the agent
        # cannot provide) through the mutability overlay; the rest of the
        # gui are its own FIELDS_TO_SAVE columns
        columns = frozenset(
            name for name, _modifier in ServicesPoolGroups.parse_save_fields(ServicesPoolGroups.FIELDS_TO_SAVE)
        )
        return gui_view.agent_definitions(
            sorted(ServicesPoolGroups.get_gui(shim, for_type), key=lambda element: element.gui.order),
            from_instance=lambda name: name not in columns,
        )

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        pool_group = typing.cast(models.ServicePoolGroup, target)
        wanted = set(names)
        snapshot: JsonObject = {}
        for name in wanted:
            if name == "name":
                snapshot[name] = pool_group.name
            elif name == "comments":
                snapshot[name] = pool_group.comments
            elif name == "priority":
                snapshot[name] = pool_group.priority
        return snapshot

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        return [d["name"] for d in self.field_definitions(for_type)]

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        fields = self.etag_fields("service_pool_group")
        item_dict: JsonObject = self.snapshot_values(target, fields)
        return item_etag(item_dict, fields)

    @typing.override
    async def op_update(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The PUT is form-shaped: image_id is a plain FIELDS_TO_SAVE entry
        # (required by fields_from_params) but hidden from the agent, and
        # pre_save indexes it directly. Merge the proposal over the
        # CAS-verified current values, injecting the stored image as
        # immutable context ("" convention: "-1" meaning none).
        # ALL the ORM work must stay out of the async context.
        def _build_params() -> tuple[str, JsonObject]:
            pool_group = typing.cast(models.ServicePoolGroup, self.resolve_target(action.target_uuid))
            params = self.snapshot_values(pool_group, self.etag_fields("service_pool_group"))
            params["image_id"] = pool_group.image.uuid if pool_group.image else "-1"
            params.update(action.values)
            return pool_group.name, params

        pool_group_name, params = await sync_to_async(_build_params, thread_sensitive=True)()
        await RestProxy().execute(
            RestTarget(
                ServicesPoolGroups,
                "gallery",
                types.rest.CustomMethodMethod.PUT,
                args=(action.target_uuid,),
            ),
            request,
            params,
        )
        return f'Service pool group "{pool_group_name}" updated'
