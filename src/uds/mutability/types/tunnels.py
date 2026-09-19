"""``tunnel.update`` / ``tunnel.create`` / ``tunnel.delete``.

A tunnel is a ``ServerGroup`` of type TUNNEL served by its own REST
endpoint (``/tunnels``), separate from ``servers/groups``. The group
holds the address the *client* sees (``host``/``port``); the child
servers are informational registrations of the external load balancer
backends, so membership (``assign`` and friends) is a relation outside
this proposal and has no functional effect on balancing.

The creation payload mirrors the administration form: ``host`` is
required and both address fields are validated at propose time with the
very validators the handler's ``pre_save`` runs.
"""

import collections.abc
import typing
from types import SimpleNamespace as _Namespace

from asgiref.sync import sync_to_async
from django.db import models as db_models

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions, ui as ui_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.core.util import validators
from uds.mcp.rest_proxy import RestProxy, RestTarget
from uds.REST.methods.tunnels_management import Tunnels

from .. import base as mutability_base
from .. import gui_view
from .. import verbs as mutability_verbs
from ..etag import item_etag

JsonObject = dict[str, typing.Any]


def _host_port_errors(values: JsonObject) -> list[str]:
    """Same checks the handler's pre_save runs (plus the port range the
    gui enforces), so a proposal that can never be applied fails at
    propose time instead of on approval."""
    errors: list[str] = []
    if "host" in values:
        try:
            validators.validate_host(str(values["host"]))
        except ui_exceptions.ValidationError as e:
            errors.append(f"field host: {e}")
    if "port" in values:
        try:
            validators.validate_port(values["port"])
        except ui_exceptions.ValidationError as e:
            errors.append(f"field port: {e}")
    return errors


class TunnelUpdate(mutability_base.MutableActionType):
    """Proposal: create, update, or delete a tunnel (tunnel-type server group)."""

    type_id = "tunnel"
    title = "Propose tunnel update"
    description = (
        "Propose changes to an existing tunnel (a tunnel-type server group; the "
        "host and port are the address clients reach through the external load "
        "balancer). The proposal does NOT apply anything: it is queued until an "
        "administrator approves it. Mutable fields are name, comments, tags, "
        "host and port. Assigned tunnel servers and transport bindings cannot "
        "be changed through this proposal."
    )
    handler = Tunnels
    model = models.ServerGroup
    noun = "Tunnel"
    # A tunnel is not a module: no subtype gallery, no data_type
    create_has_gallery = False

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            group = models.ServerGroup.objects.get(uuid__iexact=target_uuid)
        except models.ServerGroup.DoesNotExist:
            raise rest_exceptions.NotFound("Tunnel not found") from None
        if group.type != types.servers.ServerType.TUNNEL.value:
            raise rest_exceptions.NotFound("Tunnel not found")
        return group

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return "tunnel"

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        shim = typing.cast(typing.Any, _Namespace())
        # Agent view derived from the handler gui; the gui fields are
        # exactly the handler's own FIELDS_TO_SAVE columns, so any change
        # there propagates here automatically
        columns = frozenset(name for name, _modifier in Tunnels.parse_save_fields(Tunnels.FIELDS_TO_SAVE))
        return gui_view.agent_definitions(
            sorted(Tunnels.get_gui(shim, for_type), key=lambda element: element.gui.order),
            from_instance=lambda name: name not in columns,
        )

    @typing.override
    def validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        # Same checks the handler's pre_save runs, so a proposal that can
        # never be applied fails at propose time instead of on approval
        errors = super().validate_values(for_type, values, target)
        errors.extend(_host_port_errors(values))
        return errors

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        # The merged PUT carries exactly the fingerprint surface: every gui
        # field (all of them proposable), so fingerprint == definitions
        shim = typing.cast(typing.Any, _Namespace())
        return gui_view.fingerprint_names(
            sorted(Tunnels.get_gui(shim, for_type), key=lambda element: element.gui.order)
        )

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        group = typing.cast(models.ServerGroup, target)
        columns: JsonObject = {
            "name": group.name,
            "comments": group.comments,
            "tags": sorted(t.tag for t in group.tags.all()),
            "host": group.host,
            "port": group.port,
        }
        wanted = set(names)
        return {name: value for name, value in columns.items() if name in wanted}

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        fields = self.etag_fields("tunnel")
        item_dict: JsonObject = self.snapshot_values(target, fields)
        return item_etag(item_dict, fields)

    # ---------------------------------------------------- creation hooks

    @typing.override
    def tool_title(self) -> str:
        if self.operation is mutability_base.ActionOperation.CREATE:
            return mutability_verbs.create_tool_title("tunnel")
        if self.operation is mutability_base.ActionOperation.DELETE:
            return mutability_verbs.delete_tool_title("tunnel")
        return self.title

    @typing.override
    def tool_description(self) -> str:
        if self.operation is mutability_base.ActionOperation.CREATE:
            return mutability_verbs.create_tool_description(
                "tunnel",
                "name, comments, tags, host (required: IP or hostname clients "
                "reach through the external load balancer) and port (default "
                "443; validated when proposing)",
            )
        if self.operation is mutability_base.ActionOperation.DELETE:
            return mutability_verbs.delete_tool_description(
                "tunnel",
                "the REST refuses the deletion while transports are still "
                "attached to it — exactly like the administration interface.",
            )
        return self.description

    @typing.override
    def create_validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        # Tunnels have no subtype gallery: the data_type is not required
        errors = super().create_validate_values(for_type or "tunnel", values, target)
        if not str(values.get("name", "")).strip():
            errors.append("field name: required")
        if not str(values.get("host", "")).strip():
            errors.append("field host: required")
        errors.extend(_host_port_errors(values))
        return errors

    # ---------------------------------------------------------- execution

    @typing.override
    async def op_create(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The tunnels POST is form-shaped (host required, port optional):
        # an omitted port rides the gui default, never the handler's 0
        values = dict(action.values)
        params: JsonObject = {
            "name": values.get("name"),
            "comments": values.get("comments", ""),
            "tags": values.get("tags", []),
            "host": values.get("host", ""),
            "port": values.get("port", 443),
        }
        new_uuid = await mutability_verbs.execute_create(
            RestTarget(Tunnels, "tunnels", types.rest.CustomMethodMethod.POST),
            request,
            params,
        )
        return mutability_verbs.created_message("Tunnel", str(params["name"]), new_uuid)

    @typing.override
    async def op_delete(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The REST DELETE removes the row synchronously (refusing while
        # transports are attached). ALL the ORM work stays out of the
        # async context.
        group = typing.cast(
            models.ServerGroup,
            await sync_to_async(self.resolve_target, thread_sensitive=True)(action.target_uuid),
        )
        await mutability_verbs.execute_delete(
            RestTarget(
                Tunnels,
                "tunnels",
                types.rest.CustomMethodMethod.DELETE,
                args=(action.target_uuid,),
            ),
            request,
        )
        return f'Tunnel "{group.name}" deleted'

    @typing.override
    async def op_update(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # ORM work must stay out of the async context: resolve + snapshot
        # + merge in a single sync boundary
        def _build_params() -> tuple[str, JsonObject]:
            group = typing.cast(models.ServerGroup, self.resolve_target(action.target_uuid))
            params = self.snapshot_values(group, self.etag_fields("tunnel"))
            params.update(action.values)
            return group.name, params

        tunnel_name, params = await sync_to_async(_build_params, thread_sensitive=True)()
        await RestProxy().execute(
            RestTarget(
                Tunnels,
                "tunnels",
                types.rest.CustomMethodMethod.PUT,
                args=(action.target_uuid,),
            ),
            request,
            params,
        )
        return f'Tunnel "{tunnel_name}" updated'
