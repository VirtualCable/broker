"""``network.update``: propose modifications to an existing network.

Replicates the REST ``PUT /networks/{uuid}``: ``name``, ``tags`` and
``net_string`` (the range definition, validated by the handler itself).
The network model has no ``comments`` column, and its gui does not offer
the stock comments field either, so the proposable surface is exactly the
handler's ``FIELDS_TO_SAVE``.

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
from .. import gui_view
from ..etag import item_etag

JsonObject = dict[str, typing.Any]


class NetworkUpdate(mutability_base.MutableActionType):
    """Proposal: update an existing network."""

    type_id = "network"
    title = "Propose network update"
    description = (
        "Propose changes to an existing network (a range used by transports and "
        "authenticators). The proposal does NOT apply anything: it is queued until an "
        "administrator approves it. Mutable fields are name, tags and net_string "
        "(the range: subnet, interval, host, ... same formats the REST PUT accepts); "
        "use get_mutable_fields to discover them and their current values."
    )
    handler = Networks

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
        # The gui fields are exactly the handler's own FIELDS_TO_SAVE
        # columns; they come from the instance and are not proposed
        columns = frozenset(name for name, _modifier in Networks.parse_save_fields(Networks.FIELDS_TO_SAVE))
        return gui_view.agent_definitions(
            sorted(Networks.get_gui(shim, for_type), key=lambda element: element.gui.order),
            from_instance=lambda name: name not in columns,
        )

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
    async def op_update(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The PUT is form-shaped (every FIELDS_TO_SAVE entry is required):
        # merge the proposal over the CAS-verified current values, so a
        # partial proposal applies instead of failing on approval.
        # ALL the ORM work must stay out of the async context.
        def _build_params() -> tuple[str, JsonObject]:
            network = typing.cast(models.Network, self.resolve_target(action.target_uuid))
            params = self.snapshot_values(network, self.etag_fields("network"))
            params.update(action.values)
            return network.name, params

        network_name, params = await sync_to_async(_build_params, thread_sensitive=True)()
        await RestProxy().execute(
            RestTarget(
                Networks,
                "networks",
                types.rest.CustomMethodMethod.PUT,
                args=(action.target_uuid,),
            ),
            request,
            params,
        )
        return f'Network "{network_name}" updated'
