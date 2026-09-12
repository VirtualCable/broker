"""``service_pool_group.update``: propose modifications to an existing
service pool group (the "Pool Groups" shown to users).

Replicates the REST ``PUT /service-pool-groups/{uuid}``: ``name``,
``comments`` and ``priority``. The image is a foreign key to an uploaded
binary, so it is NOT offered as mutable.

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
from ..etag import item_etag
from .servers import _defs_from_gui

JsonObject = dict[str, typing.Any]


class ServicePoolGroupUpdate(mutability_base.MutableActionType):
    """Proposal: update an existing service pool group."""

    type_id = "service_pool_group.update"
    title = "Propose service pool group update"
    description = (
        "Propose changes to an existing service pool group (a folder of service pools "
        "shown to users). The proposal does NOT apply anything: it is queued until an "
        "administrator approves it. Mutable fields are name, comments and priority; "
        "use get_mutable_fields to discover them and their current values."
    )
    handler = ServicesPoolGroups

    # The image (foreign key to an uploaded binary) is not mutable
    _MODEL_FIELDS: typing.ClassVar[frozenset[str]] = frozenset({"name", "comments", "priority"})
    _EXCLUDED: typing.ClassVar[frozenset[str]] = frozenset({"image_id"})

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
        elements = [
            element
            for element in ServicesPoolGroups.get_gui(shim, for_type)
            if element.name not in self._EXCLUDED and element.gui.type != types.ui.FieldType.INFO
        ]
        return sorted(_defs_from_gui(elements, self._MODEL_FIELDS), key=lambda f: f["name"])

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
    async def execute(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # ORM work must stay out of the async context
        pool_group = typing.cast(
            models.ServicePoolGroup,
            await sync_to_async(self.resolve_target, thread_sensitive=True)(action.target_uuid),
        )
        # The PUT accepts image_id as optional (the handler's pre_save is
        # None-safe); not sending it keeps the current image untouched
        await RestProxy().execute(
            RestTarget(
                ServicesPoolGroups,
                "gallery",
                types.rest.CustomMethodMethod.PUT,
                args=(action.target_uuid,),
            ),
            request,
            dict(action.values),
        )
        return f'Service pool group "{pool_group.name}" updated'
