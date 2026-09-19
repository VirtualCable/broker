"""``network.update`` / ``network.create`` / ``network.delete``.

Replicates the REST ``PUT``/``POST``/``DELETE`` on ``/networks``:
``name``, ``tags`` and ``net_string`` (the range definition). The network
model has no ``comments`` column, and its gui does not offer the stock
comments field either, so the proposable surface is exactly the handler's
``FIELDS_TO_SAVE``.

The model saves the range through ``net.network_from_str`` WITHOUT its
check mode (an unparseable string becomes an empty range silently), so
the proposal validates the string itself the same way the gui form does,
both on create and on update. Names are unique: a duplicate fails at
execution with the REST's own error, like any other create.

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
from uds.core.util import net
from uds.mcp.rest_proxy import RestProxy, RestTarget

from .. import base as mutability_base
from .. import gui_view
from .. import verbs as mutability_verbs
from ..etag import item_etag

JsonObject = dict[str, typing.Any]


def _net_string_errors(value: typing.Any) -> list[str]:
    """Range validity, with the model's own parser (its check mode, the
    one ``Network.save`` does NOT use, so a proposal never stores a range
    that silently becomes empty)."""
    try:
        net.network_from_str(str(value), check_mode=True)
    except ValueError as e:
        return [f"field net_string: {e or 'not a valid network range'}"]
    return []


class NetworkUpdate(mutability_base.MutableActionType):
    """Proposal: create, update, or delete a network."""

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

    # A network is not a module: no subtype gallery, no data_type
    create_has_gallery = False

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
    def validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        # The handler PUT does not parse the range (pre_save is empty):
        # the model's own save silently zeroes an unparseable one, so
        # validity is checked here instead of letting the proposal apply
        errors = super().validate_values(for_type, values, target)
        if "net_string" in values:
            errors.extend(_net_string_errors(values["net_string"]))
        return errors

    # ---------------------------------------------------- creation hooks

    @typing.override
    def tool_title(self) -> str:
        if self.operation is mutability_base.ActionOperation.CREATE:
            return mutability_verbs.create_tool_title("network")
        if self.operation is mutability_base.ActionOperation.DELETE:
            return mutability_verbs.delete_tool_title("network")
        return self.title

    @typing.override
    def tool_description(self) -> str:
        if self.operation is mutability_base.ActionOperation.CREATE:
            return mutability_verbs.create_tool_description(
                "network",
                "name (unique), tags and net_string (the range: subnet, interval, "
                "host, ...; validated when proposing)",
            )
        if self.operation is mutability_base.ActionOperation.DELETE:
            return mutability_verbs.delete_tool_description(
                "network",
                "the REST removes it and detaches it from the transports and "
                "authenticators referencing it (no refusal) — exactly like the "
                "administration interface.",
            )
        return self.description

    @typing.override
    def create_validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        # Networks have no subtype gallery: the data_type is not required.
        # Both required columns are checked here (the base validation does
        # not enforce requireds), with the range parsed as the gui form does.
        errors = super().create_validate_values(for_type or "network", values, target)
        if not str(values.get("name", "")).strip():
            errors.append("field name: required")
        net_string = values.get("net_string")
        if not str(net_string or "").strip():
            errors.append("field net_string: required")
        else:
            errors.extend(_net_string_errors(net_string))
        return errors

    # ---------------------------------------------------------- execution

    @typing.override
    async def op_create(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The networks POST is form-shaped: the flat proposal maps one to
        # one (no comments column). Omitted tags ride the empty list the
        # gui sends.
        values = dict(action.values)
        params: JsonObject = {
            "name": values.get("name"),
            "net_string": values.get("net_string", ""),
            "tags": values.get("tags", []),
        }
        new_uuid = await mutability_verbs.execute_create(
            RestTarget(Networks, "networks", types.rest.CustomMethodMethod.POST),
            request,
            params,
        )
        return mutability_verbs.created_message("Network", str(params["name"]), new_uuid)

    @typing.override
    async def op_delete(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The REST DELETE removes the row synchronously, detaching it from
        # transports and authenticators (m2m, no refusal). ALL the ORM
        # work stays out of the async context.
        network = typing.cast(
            models.Network,
            await sync_to_async(self.resolve_target, thread_sensitive=True)(action.target_uuid),
        )
        await mutability_verbs.execute_delete(
            RestTarget(
                Networks,
                "networks",
                types.rest.CustomMethodMethod.DELETE,
                args=(action.target_uuid,),
            ),
            request,
        )
        return f'Network "{network.name}" deleted'

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
