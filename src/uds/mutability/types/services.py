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
from ..etag import item_etag

JsonObject = dict[str, typing.Any]

SECRET = types.ui.FieldType.PASSWORD
HIDDEN = types.ui.FieldType.HIDDEN


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

    # Columns the REST PUT reads from params (everything else in the gui
    # is a config value of the service instance).
    _MODEL_FIELDS: typing.ClassVar[frozenset[str]] = frozenset(
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
        defs: list[JsonObject] = []
        for element in self._gui(for_type, service):
            info = element.gui
            definition: JsonObject = {
                "name": element.name,
                "type": info.type.value,
                "label": info.label,
                "tooltip": info.tooltip,
                "secret": info.type in (SECRET, HIDDEN),
                "from_instance": element.name not in self._MODEL_FIELDS,
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
        # ORM work must stay out of the async context
        service = typing.cast(
            models.Service,
            await sync_to_async(self.resolve_target, thread_sensitive=True)(action.target_uuid),
        )
        # ``data_type`` is required by the REST PUT but is not mutable
        # (changing the subtype is a different operation): it is taken
        # from the target itself.
        params: JsonObject = dict(action.values)
        params["data_type"] = service.data_type
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
            target, request, params, str(service.provider.uuid)
        )
        return f'Service "{service.name}" updated'

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
