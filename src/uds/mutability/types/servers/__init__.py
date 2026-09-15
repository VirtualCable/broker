"""``server_group.update``: propose modifications to a server group.

The "Servers" tree, shown alongside providers in the administration
interface. ``server_group.update`` replicates the REST
``PUT /servers/groups/{uuid}``: ``name``, ``comments``, ``tags`` and the
load-calculation weights of the group.

Validation, serialization and execution reuse the very same handler
machinery.

The servers of a group live in the sibling ``server`` module (its own
action type, ``server.update``); the import at the bottom of this module
pulls it in so the registry discovers every family of the package.
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
from uds.REST.methods.servers_management import ServersGroups

from ... import base as mutability_base
from ... import gui_view
from ...etag import item_etag

from . import server as server

JsonObject = dict[str, typing.Any]


class ServerGroupUpdate(mutability_base.MutableActionType):
    """Proposal: update an existing server group."""

    type_id = "server_group"
    title = "Propose server group update"
    description = (
        "Propose changes to an existing server group (the container of registered/unmanaged "
        "servers). The proposal does NOT apply anything: it is queued until an administrator "
        "approves it. Mutable fields are the same the REST PUT accepts (name, comments, tags "
        "and the load calculation weights); use get_mutable_fields to discover them and "
        "their current values."
    )
    handler = ServersGroups

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.ServerGroup.objects.get(uuid__iexact=target_uuid)
        except models.ServerGroup.DoesNotExist:
            raise rest_exceptions.NotFound("Server group not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        server_group = typing.cast(models.ServerGroup, target)
        # Same shape the REST item and the gui use (TYPE@subtype)
        return f"{types.servers.ServerType(server_group.type).name}@{server_group.subtype}"

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        shim = typing.cast(typing.Any, _Namespace())
        elements = sorted(ServersGroups.get_gui(shim, for_type), key=lambda element: element.gui.order)
        # The gui names the handler PUT does NOT read from params (the
        # weights_* group) are "from instance" for the agent; they reach the
        # model through post_save. Model columns come from the handler's own
        # FIELDS_TO_SAVE (markers parsed).
        columns = frozenset(
            name for name, _modifier in ServersGroups.parse_save_fields(ServersGroups.FIELDS_TO_SAVE)
        )
        return gui_view.agent_definitions(elements, from_instance=lambda name: name not in columns)

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        server_group = typing.cast(models.ServerGroup, target)
        # Same normalization the REST item serialization uses for weights
        weights_map: JsonObject = {
            "weights_cpu": int(server_group.weights.cpu * 100 + 0.5),
            "weights_memory": int(server_group.weights.memory * 100 + 0.5),
            "weights_users": int(server_group.weights.users * 100 + 0.5),
            "weights_max_expected_users": server_group.weights.max_expected_users,
        }
        snapshot: JsonObject = {}
        for name in set(names):
            if name == "name":
                snapshot[name] = server_group.name
            elif name == "comments":
                snapshot[name] = server_group.comments
            elif name == "tags":
                snapshot[name] = sorted(t.tag for t in server_group.tags.all())  # type: ignore[attr-defined]
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
        server_group = typing.cast(models.ServerGroup, target)
        for_type = self.for_type_of(target)
        item_dict: JsonObject = {"name": server_group.name, "comments": server_group.comments}
        item_dict.update(self.snapshot_values(target, self.etag_fields(for_type)))
        return item_etag(item_dict, self.etag_fields(for_type))

    @typing.override
    async def op_update(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The PUT is form-shaped (the handler requires the whole save set,
        # weights included through post_save): merge the proposal over the
        # CAS-verified current values, so a partial proposal applies
        # instead of failing on approval.
        # ALL the ORM work must stay out of the async context.
        def _build_params() -> tuple[str, JsonObject]:
            server_group = typing.cast(models.ServerGroup, self.resolve_target(action.target_uuid))
            params = self.snapshot_values(server_group, self.etag_fields(self.for_type_of(server_group)))
            params.update(action.values)
            # ``data_type`` is required by the REST PUT but is not mutable
            # (changing the type is a different operation): taken from the target.
            params["data_type"] = self.for_type_of(server_group)
            return server_group.name, params

        group_name, params = await sync_to_async(_build_params, thread_sensitive=True)()
        target = RestTarget(
            ServersGroups,
            "servers/groups",
            types.rest.CustomMethodMethod.PUT,
            args=(action.target_uuid,),
        )
        await RestProxy().execute(target, request, params)
        return f'Server group "{group_name}" updated'
