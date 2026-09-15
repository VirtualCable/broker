"""``server.update``: propose modifications to an unmanaged server.

Replicates ``PUT /servers/groups/{uuid}/servers/{uuid}`` for *unmanaged*
servers: ``hostname``, ``ip`` and ``mac``. It is a detail of its (first)
server group, so the gui depends on the concrete target and permissions
are inherited from the parent group, exactly like services. Managed
(token-registered) servers have no editable fields through this PUT, so
they expose an empty mutable surface.

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
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.mcp.rest_proxy import RestProxy, RestTarget
from uds.REST.methods.servers_management import ServersGroups, ServersServers

from ... import base as mutability_base
from ... import gui_view
from ...etag import item_etag

JsonObject = dict[str, typing.Any]


class ServerUpdate(mutability_base.MutableActionType):
    """Proposal: update an existing (unmanaged) server of a server group."""

    type_id = "server"
    title = "Propose server update"
    description = (
        "Propose changes to an existing unmanaged server (hostname, ip or mac). The proposal "
        "does NOT apply anything: it is queued until an administrator approves it. Managed "
        "(token-registered) servers expose no mutable fields. The server must belong to a "
        "server group, from which it inherits the required permissions."
    )
    handler = ServersServers
    target_scoped_fields = True

    _MUTABLE_FIELDS: typing.ClassVar[tuple[str, ...]] = ("hostname", "ip", "mac")

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.Server.objects.get(uuid__iexact=target_uuid)
        except models.Server.DoesNotExist:
            raise rest_exceptions.NotFound("Server not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        server = typing.cast(models.Server, target)
        return f"{types.servers.ServerType(server.type).name}@{server.subtype}"

    @typing.override
    def permission_target(self, target: db_models.Model) -> db_models.Model:
        # A server is a detail of its (first) server group: MANAGEMENT is
        # checked over the parent, exactly like the REST dispatcher does.
        parent_group = self._parent_group(typing.cast(models.Server, target))
        if parent_group is None:
            raise rest_exceptions.NotFound("Server does not belong to any server group")
        return parent_group

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        server = self._require_target(target)
        parent_group = self._parent_group(server)
        # Only unmanaged servers have editable fields through the canonical PUT
        if parent_group is None or server.type != types.servers.ServerType.UNMANAGED:
            return []
        shim = typing.cast(typing.Any, _Namespace())
        elements = sorted(
            ServersServers.get_gui(shim, parent_group, for_type), key=lambda element: element.gui.order
        )
        # The detail PUT does not declare FIELDS_TO_SAVE (it is manual), so
        # the proposable set stays an explicit declaration here
        return gui_view.agent_definitions(elements, from_instance=lambda name: name not in self._MUTABLE_FIELDS)

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        server = typing.cast(models.Server, target)
        snapshot: JsonObject = {}
        for name in set(names):
            if name == "hostname":
                snapshot[name] = server.hostname
            elif name == "ip":
                snapshot[name] = server.ip
            elif name == "mac":
                snapshot[name] = "" if server.mac == models.servers.NULL_MAC else server.mac
        return snapshot

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        server = self._require_target(target)
        parent_group = self._parent_group(server)
        if parent_group is None or server.type != types.servers.ServerType.UNMANAGED:
            return []
        return list(self._MUTABLE_FIELDS)

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        for_type = self.for_type_of(target)
        item_dict = self.snapshot_values(target, self.etag_fields(for_type, target))
        return item_etag(item_dict, self.etag_fields(for_type, target))

    @typing.override
    async def op_update(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # ORM work must stay out of the async context
        server = typing.cast(
            models.Server,
            await sync_to_async(self.resolve_target, thread_sensitive=True)(action.target_uuid),
        )
        parent_group = await sync_to_async(self._parent_group, thread_sensitive=True)(server)
        if parent_group is None:
            raise ValueError("Server does not belong to any server group")
        # The detail PUT requires every field (it does not merge): proposed
        # values on top of the live ones for the untouched ones. The
        # executor's CAS check guarantees those live values are the
        # approved ones.
        params: JsonObject = self.snapshot_values(server, self._MUTABLE_FIELDS)
        params.update(action.values)
        target = RestTarget(
            ServersServers,
            "servers/groups/{uuid}/servers",
            types.rest.CustomMethodMethod.PUT,
            args=(action.target_uuid,),
            parent=RestTarget(ServersGroups, "servers/groups"),
        )
        # Detail targets need the parent uuid to resolve (and permission
        # check) the group, so the sync boundary is invoked directly.
        await sync_to_async(RestProxy._execute_sync, thread_sensitive=True)(
            target, request, params, parent_group.uuid
        )
        return f'Server "{server.hostname}" updated'

    # ------------------------------------------------------------ helpers

    def _require_target(self, target: db_models.Model | None) -> models.Server:
        if target is None:
            raise ValueError(f"{self.full_id} needs a target: its fields depend on the concrete server")
        return typing.cast(models.Server, target)

    @staticmethod
    def _parent_group(server: models.Server) -> models.ServerGroup | None:
        # Servers normally belong to a single group; tunnels (the
        # exception) get a deterministic choice
        return server.groups.order_by("id").first()
