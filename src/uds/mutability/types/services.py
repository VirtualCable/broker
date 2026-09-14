"""``service.update``: propose modifications to an existing service.

The mutable surface is an exact replica of the REST ``PUT
/providers/{uuid}/services/{uuid}``: ``name``, ``comments``, ``tags``,
``max_services_count_type`` and the gui configuration fields of the
service's data type. Unlike providers, the services gui is flat (its
config fields are not nested under ``instance``), so no flattening is
needed. Validation, serialization and execution reuse the very same
handler machinery.

Services are a detail of their provider: the gui depends on the concrete
target, and permissions are inherited from the parent provider.
"""

import collections.abc
import typing
from types import SimpleNamespace as _Namespace

from asgiref.sync import sync_to_async
from django.db import models as db_models

from uds import models
from uds.core import types
from uds.REST.methods.providers import Providers
from uds.REST.methods.services import Services
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.mcp.rest_proxy import RestProxy, RestTarget

from .. import base as mutability_base
from .. import gui_view
from ..etag import item_etag

JsonObject = dict[str, typing.Any]


class ServiceUpdate(mutability_base.MutableActionType):
    """Proposal: update an existing service of a provider."""

    type_id = "service.update"
    title = "Propose service update"
    description = (
        "Propose changes to an existing service (an offering of a provider). The proposal "
        "does NOT apply anything: it is queued until an administrator approves it. Mutable "
        "fields are the same the REST PUT accepts (name, comments, tags, "
        "max_services_count_type and the service type configuration fields); use "
        "get_mutable_fields to discover them and their current values."
    )
    handler = Services
    target_scoped_fields = True

    # Model columns the REST PUT reads from params (its save_item list,
    # minus ``data_type``, which is not mutable here). Unlike a module
    # handler, ``Services`` is a detail handler and does not declare those
    # through ``FIELDS_TO_SAVE``, so the set stays explicit here;
    # everything else in the gui is a config value of the service instance.
    _MUTABLE_COLUMNS: typing.ClassVar[frozenset[str]] = frozenset(
        {"name", "comments", "tags", "max_services_count_type"}
    )

    # ------------------------------------------------------------- hooks

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.Service.objects.get(uuid__iexact=target_uuid)
        except models.Service.DoesNotExist:
            raise rest_exceptions.NotFound("Service not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return typing.cast(models.Service, target).data_type

    @typing.override
    def permission_target(self, target: db_models.Model) -> db_models.Model:
        # A service is a detail of its provider: MANAGEMENT is held over
        # the parent, exactly like the REST dispatcher checks it.
        return typing.cast(models.Service, target).provider

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        service = self._require_target(target)
        columns = self._MUTABLE_COLUMNS
        return gui_view.agent_definitions(
            self._gui(for_type, service),
            from_instance=lambda name: name not in columns,
        )

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        service = typing.cast(models.Service, target)
        instance_values = service.get_instance(None).get_fields_as_dict()
        wanted = set(names)
        snapshot: JsonObject = {}
        for name in wanted:
            if name in instance_values:
                snapshot[name] = instance_values[name]
            elif name == "name":
                snapshot[name] = service.name
            elif name == "comments":
                snapshot[name] = service.comments
            elif name == "tags":
                snapshot[name] = sorted(t.tag for t in service.tags.all())  # type: ignore[attr-defined]
            elif name == "max_services_count_type":
                # Same string shape the REST item and the gui choice use
                snapshot[name] = str(service.max_services_count_type)
        return snapshot

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        service = self._require_target(target)
        return [element.name for element in self._gui(for_type, service)]

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        service = typing.cast(models.Service, target)
        # Same sources the REST item serialization uses for these fields
        # (model columns + flat instance values), so this fingerprint
        # tracks the handler's ETag inputs with the deterministic
        # ordering of item_etag.
        item_dict: JsonObject = {
            "name": service.name,
            "comments": service.comments,
            "tags": sorted(t.tag for t in service.tags.all()),  # pyright: ignore[reportAttributeAccessIssue]
            "max_services_count_type": str(service.max_services_count_type),
            **service.get_instance(None).get_fields_as_dict(),
        }
        return item_etag(item_dict, self.etag_fields(self.for_type_of(target), target))

    @typing.override
    async def execute(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The PUT is form-shaped (save_item requires the whole save set and
        # serializes the instance from the flat params), so a partial
        # proposal is merged over the CAS-verified current values. The
        # services gui is flat, so no nesting is needed.
        # ALL the ORM work must stay out of the async context.
        def _build_params() -> tuple[str, str, JsonObject]:
            service = typing.cast(models.Service, self.resolve_target(action.target_uuid))
            for_type = self.for_type_of(service)
            params: JsonObject = self.snapshot_values(service, self.etag_fields(for_type, service))
            params.update(action.values)
            # ``data_type`` is required by the REST PUT but is not mutable
            # (changing the subtype is a different operation)
            params["data_type"] = for_type
            return service.name, str(service.provider.uuid), params

        service_name, provider_uuid, params = await sync_to_async(_build_params, thread_sensitive=True)()
        target = RestTarget(
            Services,
            "providers/{uuid}/services",
            types.rest.CustomMethodMethod.PUT,
            args=(action.target_uuid,),
            parent=RestTarget(Providers, "providers"),
        )
        # Detail targets need the parent uuid to resolve (and permission
        # check) the provider, so the sync boundary is invoked directly.
        await sync_to_async(RestProxy._execute_sync, thread_sensitive=True)(
            target, request, params, provider_uuid
        )
        return f'Service "{service_name}" updated'

    # ------------------------------------------------------------ helpers

    def _require_target(self, target: db_models.Model | None) -> models.Service:
        if target is None:
            raise ValueError(f"{self.type_id} needs a target: its fields depend on the concrete service")
        return typing.cast(models.Service, target)

    def _gui(self, for_type: str, service: models.Service) -> list[types.ui.GuiElement]:
        """Gui elements of one service type, without the auth machinery.

        ``Services.get_gui`` is request-independent; binding it to an
        empty namespace avoids instantiating the full handler (whose
        parent chain resolves authentication).
        """
        shim = typing.cast(typing.Any, _Namespace())
        return sorted(Services.get_gui(shim, service.provider, for_type), key=lambda f: f.gui.order)
