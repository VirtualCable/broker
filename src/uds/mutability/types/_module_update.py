"""Shared machinery for action types backed by a module model.

``ModuleUpdateActionType`` covers the action types whose target is a
model with a dynamically rendered gui (``transport.update``,
``osmanager.update``, ``mfa.update``, ``notifier.update``,
``authenticator.update``, ...). The gui is the one the REST handler
renders for the concrete type: only model columns and module
configuration fields are mutable; m2m relations and FK references stay
out of the surface.

Execution mirrors the handler PUT. Those PUTs are form-shaped (most
``FIELDS_TO_SAVE`` entries are required), so the proposal is merged over
the CAS-verified current values: any drift between proposal and approval
has already revoked the action, so the merged payload is exactly what
the administrator approved.

Validation, serialization and execution reuse the very same handler
machinery.
"""

import collections.abc
import typing
from types import SimpleNamespace as _Namespace

from asgiref.sync import sync_to_async
from django.core.exceptions import ObjectDoesNotExist
from django.db import models as db_models

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.mcp.rest_proxy import RestProxy, RestTarget
from uds.REST.model.master import ModelHandler

from .. import base as mutability_base
from ..etag import item_etag
from .servers import _defs_from_gui

JsonObject = dict[str, typing.Any]


class ModuleUpdateActionType(mutability_base.MutableActionType):
    """Proposal: update a module-backed model through its REST handler."""

    # Subclasses MUST set: handler, model, collection, noun
    # Narrows the inherited handler to the concrete ModelHandler subclass.
    # Safe: the attribute is declared once per class and only ever read.
    # pyright: ignore[reportIncompatibleVariableOverride]
    handler: typing.ClassVar[type[ModelHandler[typing.Any]]]  # pyright: ignore[reportIncompatibleVariableOverride]
    model: typing.ClassVar[type[models.ManagedObjectModel]]

    # REST collection path used by the synthetic PUT
    collection: typing.ClassVar[str]
    # Human readable kind for the action summary
    noun: typing.ClassVar[str]

    # Model columns the REST PUT reads from params (the rest of the gui
    # are configuration fields of the module instance)
    model_fields: typing.ClassVar[frozenset[str]] = frozenset({"name", "comments", "tags"})
    # Gui elements that are not part of the mutable surface (m2m
    # relations, FK references)
    excluded: typing.ClassVar[frozenset[str]] = frozenset({"networks", "pools"})
    # Per-field adapters to convert a model column value into the shape
    # the REST PUT expects (e.g. CSV storage -> list)
    snapshot_adapters: typing.ClassVar[dict[str, collections.abc.Callable[[typing.Any], typing.Any]]] = {}

    # ------------------------------------------------------------- hooks

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return self.model.objects.get(uuid__iexact=target_uuid)
        except ObjectDoesNotExist:
            raise rest_exceptions.NotFound(f"{self.noun} not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return typing.cast(models.ManagedObjectModel, target).data_type

    # --------------------------------------------------- gui & snapshots

    def _gui_elements(self, for_type: str) -> list[types.ui.GuiElement]:
        shim = typing.cast(typing.Any, _Namespace())
        elements = (
            element
            for element in self.handler.get_gui(shim, for_type)
            if element.name not in self.excluded and element.gui.type != types.ui.FieldType.INFO
        )
        return sorted(elements, key=lambda e: e.gui.order)

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        return _defs_from_gui(self._gui_elements(for_type), self.model_fields)

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        return [d["name"] for d in self.field_definitions(for_type)]

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        module = typing.cast(models.ManagedObjectModel, target)
        instance_values = module.get_instance(None).get_fields_as_dict()
        snapshot: JsonObject = {}
        for name in names:
            if name in instance_values:
                value: typing.Any = instance_values[name]
            elif name == "tags":
                # TaggingMixin is mixed into every model handled here, but
                # the declared base type does not expose the relation
                any_module = typing.cast(typing.Any, module)
                value = sorted(t.tag for t in any_module.tags.all())
            else:
                # Model column (name, comments, priority, level, ...)
                value = getattr(module, name)
            adapter = self.snapshot_adapters.get(name)
            snapshot[name] = adapter(value) if adapter else value
        return snapshot

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        fields = self.etag_fields(self.for_type_of(target))
        item_dict: JsonObject = self.snapshot_values(target, fields)
        return item_etag(item_dict, fields)

    # ---------------------------------------------------------- execution

    @typing.override
    async def execute(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # ALL the ORM work (target, instance values, tags) must stay out
        # of the async context: resolve + merge in one sync boundary.
        def _build_params() -> tuple[str, JsonObject]:
            target = typing.cast(models.ManagedObjectModel, self.resolve_target(action.target_uuid))
            for_type = self.for_type_of(target)
            # The PUT is form-shaped: merge the proposal over the
            # CAS-verified current values
            params: JsonObject = self.snapshot_values(target, self.etag_fields(for_type))
            params.update(action.values)
            # ``data_type`` is required by the REST PUT but is not mutable
            params["data_type"] = for_type
            return target.name, params

        name, params = await sync_to_async(_build_params, thread_sensitive=True)()
        await RestProxy().execute(
            RestTarget(
                self.handler,
                f"{self.collection}/{{uuid}}",
                types.rest.CustomMethodMethod.PUT,
                args=(action.target_uuid,),
            ),
            request,
            params,
        )
        return f'{self.noun} "{name}" updated'
