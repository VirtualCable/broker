"""Shared machinery for action types backed by a module model.

``ModuleUpdateActionType`` covers the action types whose target is a
model with a dynamically rendered gui (``transport.update``,
``osmanager.update``, ``mfa.update``, ``notifier.update``,
``authenticator.update``, ...). The gui is the one the REST handler
renders for the concrete type, and it is the single source of truth of
the mutable surface: model columns, module configuration fields and the
non-mutable elements (m2m relations, FK references) distinguished by
their ``FieldMutability`` overlay (``hidden``) / readonly flag. GUI
changes propagate to the agent automatically.

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
from .. import gui_view
from .. import verbs as mutability_verbs
from ..etag import item_etag

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

    @classmethod
    def params_columns(cls) -> frozenset[str]:
        """Model columns the handler PUT reads from params.

        This is the handler's own ``FIELDS_TO_SAVE`` (field names, without
        their ``:default`` optional-marker suffix): the same contract the
        snapshot honors. A handler gaining a column propagates to the
        agent surface without any edit here.
        """
        return frozenset(name for name, _modifier in cls.handler.parse_save_fields(cls.handler.FIELDS_TO_SAVE))

    def _gui_elements(self, for_type: str) -> list[types.ui.GuiElement]:
        """The handler gui of the subtype, ordered as the builder declared.

        No filtering here: what reaches the agent (and the fingerprint) is
        decided by ``gui_view`` from the elements' own annotations (hidden
        overlay, readonly flag, INFO type).
        """
        shim = typing.cast(typing.Any, _Namespace())
        return sorted(self.handler.get_gui(shim, for_type), key=lambda element: element.gui.order)

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        columns = self.params_columns()
        return gui_view.agent_definitions(
            self._gui_elements(for_type),
            from_instance=lambda name: name not in columns,
        )

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        # The merged PUT carries exactly the proposable surface (module
        # PUTs need no context fields), so fingerprint == definitions
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
    async def op_update(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
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

    # ---------------------------------------------------- creation (root)

    @typing.override
    def tool_title(self) -> str:
        if self.operation is mutability_base.ActionOperation.CREATE:
            return mutability_verbs.create_tool_title(self.noun.lower())
        if self.operation is mutability_base.ActionOperation.DELETE:
            return mutability_verbs.delete_tool_title(self.noun.lower())
        return self.title

    @typing.override
    def tool_description(self) -> str:
        if self.operation is mutability_base.ActionOperation.CREATE:
            return mutability_verbs.create_tool_description(
                self.noun.lower(),
                "the module form fields (model columns and the type configuration "
                "fields; the declared defaults fill any field the proposal omits)",
                gallery=self.type_id,
            )
        if self.operation is mutability_base.ActionOperation.DELETE:
            return mutability_verbs.delete_tool_description(
                self.noun.lower(),
                "the module stops being available (everything still referencing it "
                "refuses the deletion at the REST handler), like the administration "
                "interface does.",
            )
        return self.description

    @typing.override
    async def op_create(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The module POST is form-shaped: model columns travel in params
        # (the handler FIELDS_TO_SAVE), the module configuration under
        # "instance", and the subtype as data_type. Omitted columns fall
        # back to the gui default; omitted comments/tags to empty. ALL
        # the work building the payload (the gui rendering touches the
        # module environment) must stay out of the async context.
        def _build_params() -> JsonObject:
            values = dict(action.values)
            columns = self.params_columns()
            params: JsonObject = {
                "name": values.get("name"),
                "comments": values.get("comments", ""),
                "tags": values.get("tags", []),
            }
            instance: JsonObject = {}
            for name, value in values.items():
                if name in self._RESERVED_CREATE_KEYS or name in ("name", "comments", "tags"):
                    continue
                if name in columns:
                    params[name] = value
                else:
                    instance[name] = value
            for_type = self.create_for_type(values)
            definitions = {d["name"]: d for d in self.create_field_definitions(for_type, None)}
            for name in columns:
                if name in params:
                    continue
                default = definitions.get(name, {}).get("default")
                if default is not None:
                    params[name] = default
            # Always present (even empty): without it the handler would
            # fall back to using ALL params as module configuration
            params["instance"] = instance
            params["data_type"] = for_type
            return params

        params = await sync_to_async(_build_params, thread_sensitive=True)()
        new_uuid = await mutability_verbs.execute_create(
            RestTarget(self.handler, self.collection, types.rest.CustomMethodMethod.POST),
            request,
            params,
        )
        return mutability_verbs.created_message(self.noun, str(params["name"]), new_uuid)

    @typing.override
    async def op_delete(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The REST DELETE removes the row synchronously (refusing while
        # anything still references the module), exactly like the
        # administration interface. ALL the ORM work stays out of the
        # async context.
        target = typing.cast(
            models.ManagedObjectModel,
            await sync_to_async(self.resolve_target, thread_sensitive=True)(action.target_uuid),
        )
        await mutability_verbs.execute_delete(
            RestTarget(
                self.handler,
                self.collection,
                types.rest.CustomMethodMethod.DELETE,
                args=(action.target_uuid,),
            ),
            request,
        )
        return f'{self.noun} "{target.name}" deleted'
