"""``service``: create and update services of a provider.

``service.update`` replicates the REST ``PUT
/providers/{uuid}/services/{uuid}``: ``name``, ``comments``, ``tags``,
``max_services_count_type`` and the gui configuration fields of the
service's data type. Unlike providers, the services gui is flat (its
config fields are not nested under ``instance``), so no flattening is
needed. ``service.create`` proposes a NEW service of a provider
(``POST /providers/{uuid}/services``): the proposal targets the PARENT
provider (MANAGEMENT over it is the create permission, exactly the REST
detail semantics), carries the subtype plus the initial values and no
CAS (nothing exists yet); the real uuid is assigned at execution.

Validation, serialization and execution reuse the very same handler
machinery.

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

from ... import base as mutability_base
from ... import gui_view
from ...etag import item_etag

JsonObject = dict[str, typing.Any]


class ServiceUpdate(mutability_base.MutableActionType):
    """Proposals over the services of a provider (update, create)."""

    type_id = "service"
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
    # A creation is a detail of the parent provider: the proposal targets
    # the provider uuid and MANAGEMENT over it is the create permission.
    create_needs_parent = True

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
        service = typing.cast(models.Service, self._require_target(target))
        columns = self._MUTABLE_COLUMNS
        return gui_view.agent_definitions(
            self._gui(for_type, service.provider),
            from_instance=lambda name: name not in columns,
        )

    @typing.override
    def resolve_create_parent(self, parent_uuid: str) -> db_models.Model:
        # The container of a service creation is its provider
        try:
            return models.Provider.objects.get(uuid__iexact=parent_uuid)
        except models.Provider.DoesNotExist:
            raise rest_exceptions.NotFound("Provider not found") from None

    @typing.override
    def create_field_definitions(
        self, for_type: str, target: db_models.Model | None = None
    ) -> list[JsonObject]:
        # The creation gui is built from the parent provider (the target
        # of the proposal) plus the declared subtype, without a live
        # service instance.
        provider = typing.cast(models.Provider, self._require_target(target))
        columns = self._MUTABLE_COLUMNS
        return gui_view.agent_definitions(
            self._gui(for_type, provider),
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
        service = typing.cast(models.Service, self._require_target(target))
        return [element.name for element in self._gui(for_type, service.provider)]

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
    async def op_update(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
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

    @typing.override
    def tool_title(self) -> str:
        if self.operation is mutability_base.ActionOperation.CREATE:
            return "Propose creating a service"
        if self.operation is mutability_base.ActionOperation.DELETE:
            return "Propose deleting a service"
        return self.title

    @typing.override
    def tool_description(self) -> str:
        if self.operation is mutability_base.ActionOperation.CREATE:
            return (
                "Propose creating a NEW service of a provider (target_uuid is the PROVIDER's "
                "uuid). The values are the subtype (data_type, one of the "
                "get_creatable_types listing for this provider) plus the initial values "
                "of the service form fields (name, comments, tags, max_services_count_type "
                "and the type configuration fields; use get_mutable_fields with this "
                "action type, the provider uuid and the for_type to discover them). "
                "There is no live state to conflict with, so the proposal carries no "
                "freshness checks, and it does NOT create anything: it is queued until "
                "an administrator approves it. The real uuid is assigned at execution "
                "and reported back in the result."
            )
        if self.operation is mutability_base.ActionOperation.DELETE:
            return (
                "Propose deleting a service of a provider. The service stops being "
                "offered (service pools still using it refuse the deletion at the "
                "REST handler), like the administration interface does. No fields "
                "are needed: the service itself is the target of the proposal, and "
                "any change to it after the proposal was taken cuts and denies the "
                "flow. The proposal does NOT delete anything: it is queued until an "
                "administrator approves it."
            )
        return self.description

    @typing.override
    async def op_create(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The POST is form-shaped and the services gui is flat: the
        # proposal values map one to one (omitted comments/tags fall
        # back to empty). ALL the ORM work stays out of the async context.
        def _build_params() -> tuple[str, str, JsonObject]:
            # The target of a creation proposal IS the parent provider
            provider = self.resolve_create_parent(action.target_uuid)
            values = dict(action.values)
            params: JsonObject = {
                "name": values.get("name"),
                "comments": values.get("comments", ""),
                "tags": values.get("tags", []),
            }
            for name, value in values.items():
                if name in self._RESERVED_CREATE_KEYS or name in ("name", "comments", "tags"):
                    continue
                params[name] = value
            params["data_type"] = self.create_for_type(values)
            return str(params["name"]), str(typing.cast(models.Provider, provider).uuid), params

        name, provider_uuid, params = await sync_to_async(_build_params, thread_sensitive=True)()
        target = RestTarget(
            Services,
            "providers/{uuid}/services",
            types.rest.CustomMethodMethod.POST,
            parent=RestTarget(Providers, "providers"),
        )
        # Detail targets need the parent uuid to resolve (and permission
        # check) the provider, so the sync boundary is invoked directly.
        response = await sync_to_async(RestProxy._execute_sync, thread_sensitive=True)(
            target, request, params, provider_uuid
        )
        new_uuid: typing.Any = None
        if isinstance(response, dict):
            new_uuid = typing.cast("JsonObject", response).get("id")
        return f'Service "{name}" created' + (f" (uuid {new_uuid})" if new_uuid else "")

    @typing.override
    async def op_delete(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The REST DELETE removes the row synchronously (service pools
        # referencing the service refuse the deletion at the handler).
        # ALL the ORM work stays out of the async context.
        def _resolve() -> tuple[str, str]:
            service = typing.cast(models.Service, self.resolve_target(action.target_uuid))
            return service.name, service.provider.uuid

        service_name, provider_uuid = await sync_to_async(_resolve, thread_sensitive=True)()
        await sync_to_async(RestProxy._execute_sync, thread_sensitive=True)(
            RestTarget(
                Services,
                "providers/{uuid}/services",
                types.rest.CustomMethodMethod.DELETE,
                args=(action.target_uuid,),
                parent=RestTarget(Providers, "providers"),
            ),
            request,
            {},
            provider_uuid,
        )
        return f'Service "{service_name}" deleted'

    # ------------------------------------------------------------ helpers

    def _require_target(self, target: db_models.Model | None) -> db_models.Model:
        if target is None:
            raise ValueError(f"{self.full_id} needs a target: its fields depend on the concrete service")
        return target

    def _gui(self, for_type: str, provider: db_models.Model) -> list[types.ui.GuiElement]:
        """Gui elements of one service type, without the auth machinery.

        ``Services.get_gui`` is request-independent; binding it to an
        empty namespace avoids instantiating the full handler (whose
        parent chain resolves authentication).
        """
        shim = typing.cast(typing.Any, _Namespace())
        return sorted(Services.get_gui(shim, provider, for_type), key=lambda f: f.gui.order)
