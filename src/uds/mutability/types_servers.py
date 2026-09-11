"""``server_group.update`` and ``server.update``: propose modifications to
the server infrastructure (the "Servers" tree, shown alongside providers
in the administration interface).

``server_group.update`` replicates the REST ``PUT /servers/groups/{uuid}``:
``name``, ``comments``, ``tags`` and the load-calculation weights of the
group.

``server.update`` replicates ``PUT /servers/groups/{uuid}/servers/{uuid}``
for *unmanaged* servers: ``hostname``, ``ip`` and ``mac``. It is a detail
of its (first) server group, so the gui depends on the concrete target and
permissions are inherited from the parent group, exactly like services.
Managed (token-registered) servers have no editable fields through this
PUT, so they expose an empty mutable surface.

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
from uds.REST.methods.servers_management import ServersGroups, ServersServers
from uds.core.exceptions import rest as rest_exceptions
from uds.mcp.rest_proxy import RestProxy, RestTarget

from . import base as mutability_base
from .etag import item_etag

JsonObject = dict[str, typing.Any]


def _defs_from_gui(
    elements: list[types.ui.GuiElement], model_fields: collections.abc.Set[str]
) -> list[JsonObject]:
    """Field definitions out of gui elements (info fields are not mutable)."""
    defs: list[JsonObject] = []
    for element in elements:
        info = element.gui
        if info.type == types.ui.FieldType.INFO:
            continue
        definition: JsonObject = {
            "name": element.name,
            "type": info.type.value,
            "label": info.label,
            "tooltip": info.tooltip,
            "secret": info.type in (types.ui.FieldType.PASSWORD, types.ui.FieldType.HIDDEN),
            "from_instance": element.name not in model_fields,
        }
        if info.required:
            definition["required"] = True
        if info.readonly:
            definition["readonly"] = True
        if info.default is not None:
            definition["default"] = info.default() if callable(info.default) else info.default
        if info.min_value is not None:
            definition["min"] = info.min_value
        if info.max_value is not None:
            definition["max"] = info.max_value
        if info.length is not None:
            definition["length"] = info.length
        if info.pattern != types.ui.FieldPatternType.NONE:
            definition["pattern"] = str(info.pattern)
        choices: typing.Any = info.choices
        if isinstance(choices, (list, tuple)):
            definition["choices"] = typing.cast("list[typing.Any]", choices)
        if info.tab:
            definition["tab"] = str(info.tab)
        defs.append(definition)
    return defs


class ServerGroupUpdate(mutability_base.MutableActionType):
    """Proposal: update an existing server group."""

    type_id = "server_group.update"
    title = "Propose server group update"
    description = (
        "Propose changes to an existing server group (the container of registered/unmanaged "
        "servers). The proposal does NOT apply anything: it is queued until an administrator "
        "approves it. Mutable fields are the same the REST PUT accepts (name, comments, tags "
        "and the load calculation weights); use get_mutable_fields to discover them and "
        "their current values."
    )
    handler = ServersGroups

    # Columns the REST PUT reads from params; the rest of the gui
    # (weights_*) lands on the group's "weights" property via post_save.
    _MODEL_FIELDS: typing.ClassVar[frozenset[str]] = frozenset({"name", "comments", "tags"})

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.ServerGroup.objects.get(uuid__iexact=target_uuid)
        except models.ServerGroup.DoesNotExist:
            raise rest_exceptions.NotFound("Server group not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        group = typing.cast(models.ServerGroup, target)
        # Same shape the REST item and the gui use (TYPE@subtype)
        return f"{types.servers.ServerType(group.type).name}@{group.subtype}"

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        shim = typing.cast(typing.Any, _Namespace())
        elements = sorted(ServersGroups.get_gui(shim, for_type), key=lambda f: f.gui.order)
        return _defs_from_gui(elements, self._MODEL_FIELDS)

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        group = typing.cast(models.ServerGroup, target)
        # Same normalization the REST item serialization uses for weights
        weights_map: JsonObject = {
            "weights_cpu": int(group.weights.cpu * 100 + 0.5),
            "weights_memory": int(group.weights.memory * 100 + 0.5),
            "weights_users": int(group.weights.users * 100 + 0.5),
            "weights_max_expected_users": group.weights.max_expected_users,
        }
        snapshot: JsonObject = {}
        for name in set(names):
            if name == "name":
                snapshot[name] = group.name
            elif name == "comments":
                snapshot[name] = group.comments
            elif name == "tags":
                snapshot[name] = sorted(t.tag for t in group.tags.all())  # type: ignore[attr-defined]
            else:
                value = weights_map.get(name)
                if value is not None:
                    snapshot[name] = value
        return snapshot

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        return [d["name"] for d in self.field_definitions(for_type)]

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        group = typing.cast(models.ServerGroup, target)
        for_type = self.for_type_of(target)
        item_dict: JsonObject = {"name": group.name, "comments": group.comments}
        item_dict.update(self.snapshot_values(target, self.etag_fields(for_type)))
        return item_etag(item_dict, self.etag_fields(for_type))

    @typing.override
    async def execute(self, action: "models.FlowAction", request: typing.Any) -> str:
        # ORM work must stay out of the async context
        group = typing.cast(
            models.ServerGroup,
            await sync_to_async(self.resolve_target, thread_sensitive=True)(action.target_uuid),
        )
        # ``data_type`` is required by the REST PUT but is not mutable
        # (changing the type is a different operation): taken from the target.
        params: JsonObject = dict(action.values)
        params["data_type"] = self.for_type_of(group)
        target = RestTarget(
            ServersGroups,
            "servers/groups",
            types.rest.CustomMethodMethod.PUT,
            args=(action.target_uuid,),
        )
        await RestProxy().execute(target, request, params)
        return f'Server group "{group.name}" updated'


class ServerUpdate(mutability_base.MutableActionType):
    """Proposal: update an existing (unmanaged) server of a server group."""

    type_id = "server.update"
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
        group = self._parent_group(typing.cast(models.Server, target))
        if group is None:
            raise rest_exceptions.NotFound("Server does not belong to any server group")
        return group

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        server = self._require_target(target)
        group = self._parent_group(server)
        # Only unmanaged servers have editable fields through the canonical PUT
        if group is None or server.type != types.servers.ServerType.UNMANAGED:
            return []
        shim = typing.cast(typing.Any, _Namespace())
        elements = sorted(ServersServers.get_gui(shim, group, for_type), key=lambda f: f.gui.order)
        return _defs_from_gui(elements, set(self._MUTABLE_FIELDS))

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
        group = self._parent_group(server)
        if group is None or server.type != types.servers.ServerType.UNMANAGED:
            return []
        return list(self._MUTABLE_FIELDS)

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        for_type = self.for_type_of(target)
        item_dict = self.snapshot_values(target, self.etag_fields(for_type, target))
        return item_etag(item_dict, self.etag_fields(for_type, target))

    @typing.override
    async def execute(self, action: "models.FlowAction", request: typing.Any) -> str:
        # ORM work must stay out of the async context
        server = typing.cast(
            models.Server,
            await sync_to_async(self.resolve_target, thread_sensitive=True)(action.target_uuid),
        )
        group = await sync_to_async(self._parent_group, thread_sensitive=True)(server)
        if group is None:
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
        await sync_to_async(RestProxy._execute_sync, thread_sensitive=True)(target, request, params, group.uuid)
        return f'Server "{server.hostname}" updated'

    # ------------------------------------------------------------ helpers

    def _require_target(self, target: db_models.Model | None) -> models.Server:
        if target is None:
            raise ValueError(f"{self.type_id} needs a target: its fields depend on the concrete server")
        return typing.cast(models.Server, target)

    @staticmethod
    def _parent_group(server: models.Server) -> models.ServerGroup | None:
        # Servers normally belong to a single group; tunnels (the
        # exception) get a deterministic choice
        return server.groups.order_by("id").first()
