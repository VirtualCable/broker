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
from ... import verbs as mutability_verbs
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
    # A creation is a detail of its server group: the proposal targets
    # the group uuid and MANAGEMENT over it is the create permission.
    create_needs_parent = True

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

    @typing.override
    def tool_title(self) -> str:
        if self.operation is mutability_base.ActionOperation.CREATE:
            return mutability_verbs.create_tool_title("server")
        if self.operation is mutability_base.ActionOperation.DELETE:
            return mutability_verbs.delete_tool_title("server")
        return self.title

    @typing.override
    def tool_description(self) -> str:
        if self.operation is mutability_base.ActionOperation.CREATE:
            return mutability_verbs.create_tool_description(
                "unmanaged server inside a server group",
                "hostname, ip and optional mac",
                needs_parent=True,
                extra=(
                    "Only UNMANAGED groups accept new servers this way (managed "
                    "groups attach already-registered servers instead, not "
                    "supported here)."
                ),
            )
        if self.operation is mutability_base.ActionOperation.DELETE:
            return mutability_verbs.delete_tool_description(
                "server of a server group",
                "for UNMANAGED groups the server record itself is deleted; for "
                "managed groups the server is just detached from the group (it "
                "stays registered), exactly like the administration interface.",
            )
        return self.description

    # ----------------------------------------------------- creation hooks

    @typing.override
    def resolve_create_parent(self, parent_uuid: str) -> db_models.Model:
        # The container of a server creation is its server group
        try:
            return models.ServerGroup.objects.get(uuid__iexact=parent_uuid)
        except models.ServerGroup.DoesNotExist:
            raise rest_exceptions.NotFound("Server group not found") from None

    @typing.override
    def create_field_definitions(
        self, for_type: str, target: db_models.Model | None = None
    ) -> list[JsonObject]:
        # The creation gui is built from the parent group (the target of
        # the proposal). Only UNMANAGED groups accept new servers through
        # this verb: managed groups attach existing servers instead, a
        # different operation with no form fields here (empty surface
        # rejects any proposal with those groups as container).
        group = self._require_parent(target)
        if group.type != types.servers.ServerType.UNMANAGED:
            return []
        shim = typing.cast(typing.Any, _Namespace())
        elements = sorted(ServersServers.get_gui(shim, group, for_type), key=lambda element: element.gui.order)
        return gui_view.agent_definitions(elements, from_instance=lambda name: name not in self._MUTABLE_FIELDS)

    @typing.override
    async def op_create(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The POST is form-shaped and requires every field key present
        # (mac is optional and defaults to empty). ALL the ORM work stays
        # out of the async context.
        def _build_params() -> tuple[str, str, JsonObject]:
            group = typing.cast(models.ServerGroup, self.resolve_create_parent(action.target_uuid))
            values = dict(action.values)
            params: JsonObject = {
                "hostname": values.get("hostname"),
                "ip": values.get("ip"),
                "mac": str(values.get("mac", "") or "").strip().upper(),
            }
            return str(params["hostname"]), group.uuid, params

        hostname, group_uuid, params = await sync_to_async(_build_params, thread_sensitive=True)()
        new_uuid = await mutability_verbs.execute_create(
            RestTarget(
                ServersServers,
                "servers/groups/{uuid}/servers",
                types.rest.CustomMethodMethod.POST,
                parent=RestTarget(ServersGroups, "servers/groups"),
            ),
            request,
            params,
            group_uuid,
        )
        return mutability_verbs.created_message("Server", hostname, new_uuid)

    @typing.override
    async def op_delete(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The REST DELETE detaches the server from its group (deleting
        # the record for UNMANAGED groups, just the reference for managed
        # ones). ALL the ORM work must stay out of the async context.
        def _resolve() -> tuple[str, str]:
            server = typing.cast(models.Server, self.resolve_target(action.target_uuid))
            parent_group = self._parent_group(server)
            if parent_group is None:
                raise ValueError("Server does not belong to any server group")
            return server.hostname, parent_group.uuid

        hostname, group_uuid = await sync_to_async(_resolve, thread_sensitive=True)()
        await mutability_verbs.execute_delete(
            RestTarget(
                ServersServers,
                "servers/groups/{uuid}/servers",
                types.rest.CustomMethodMethod.DELETE,
                args=(action.target_uuid,),
                parent=RestTarget(ServersGroups, "servers/groups"),
            ),
            request,
            group_uuid,
        )
        return f'Server "{hostname}" deleted'

    # ------------------------------------------------------------ helpers

    def _require_parent(self, target: db_models.Model | None) -> models.ServerGroup:
        if target is None:
            raise ValueError(f"{self.full_id} needs its parent server group for creation")
        return typing.cast(models.ServerGroup, target)

    def _require_target(self, target: db_models.Model | None) -> models.Server:
        if target is None:
            raise ValueError(f"{self.full_id} needs a target: its fields depend on the concrete server")
        return typing.cast(models.Server, target)

    @staticmethod
    def _parent_group(server: models.Server) -> models.ServerGroup | None:
        # Servers normally belong to a single group; tunnels (the
        # exception) get a deterministic choice
        return server.groups.order_by("id").first()
