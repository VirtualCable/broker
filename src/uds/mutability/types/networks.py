"""``network.update``: propose modifications to an existing network.

Replicates the REST ``PUT /networks/{uuid}``: ``name``, ``tags`` and
``net_string`` (the range definition, validated by the handler itself).
Note the handler's ``FIELDS_TO_SAVE`` does not include ``comments``: the
stock gui field is cosmetic there, so it is NOT offered as mutable.

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
from uds.REST.methods.networks import Networks
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.mcp.rest_proxy import RestProxy, RestTarget

from .. import base as mutability_base
from ..etag import item_etag
from .servers import _defs_from_gui

JsonObject = dict[str, typing.Any]


class NetworkUpdate(mutability_base.MutableActionType):
    """Proposal: update an existing network."""

    type_id = "network.update"
    title = "Propose network update"
    description = (
        "Propose changes to an existing network (a range used by transports and "
        "authenticators). The proposal does NOT apply anything: it is queued until an "
        "administrator approves it. Mutable fields are name, tags and net_string "
        "(the range: subnet, interval, host, ... same formats the REST PUT accepts); "
        "use get_mutable_fields to discover them and their current values."
    )
    handler = Networks

    # The handler only persists these (its FIELDS_TO_SAVE); the stock
    # comments field of the gui is cosmetic and is excluded
    _MODEL_FIELDS: typing.ClassVar[frozenset[str]] = frozenset({"name", "net_string", "tags"})
    _EXCLUDED: typing.ClassVar[frozenset[str]] = frozenset({"comments"})

    # ------------------------------------------------------------- hooks

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.Network.objects.get(uuid__iexact=target_uuid)
        except models.Network.DoesNotExist:
            raise rest_exceptions.NotFound("Network not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return "network"

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        shim = typing.cast(typing.Any, _Namespace())
        elements = [
            element
            for element in Networks.get_gui(shim, for_type)
            if element.name not in self._EXCLUDED and element.gui.type != types.ui.FieldType.INFO
        ]
        return sorted(_defs_from_gui(elements, self._MODEL_FIELDS), key=lambda f: f["name"])

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        network = typing.cast(models.Network, target)
        wanted = set(names)
        snapshot: JsonObject = {}
        for name in wanted:
            if name == "name":
                snapshot[name] = network.name
            elif name == "net_string":
                snapshot[name] = network.net_string
            elif name == "tags":
                snapshot[name] = sorted(t.tag for t in network.tags.all())  # type: ignore[attr-defined]
        return snapshot

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        return [d["name"] for d in self.field_definitions(for_type)]

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        fields = self.etag_fields("network")
        item_dict: JsonObject = self.snapshot_values(target, fields)
        return item_etag(item_dict, fields)

    @typing.override
    async def execute(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # ORM work must stay out of the async context
        network = typing.cast(
            models.Network,
            await sync_to_async(self.resolve_target, thread_sensitive=True)(action.target_uuid),
        )
        await RestProxy().execute(
            RestTarget(
                Networks,
                "networks",
                types.rest.CustomMethodMethod.PUT,
                args=(action.target_uuid,),
            ),
            request,
            dict(action.values),
        )
        return f'Network "{network.name}" updated'
