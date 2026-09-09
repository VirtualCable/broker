"""``provider.update``: propose modifications to an existing provider.

The mutable surface is an exact replica of the REST ``PUT
/providers/{uuid}``: ``name``, ``comments``, ``tags`` and the gui
configuration fields of the provider's data type. Validation,
serialization and execution reuse the very same handler machinery.
"""

import collections.abc
import typing
from types import SimpleNamespace as _Namespace

from django.db import models as db_models

from uds import models
from uds.core import types
from uds.REST.methods.providers import Providers
from uds.core.exceptions import rest as rest_exceptions
from uds.mcp.rest_proxy import RestProxy, RestTarget

from . import base as mutability_base
from .etag import item_etag

JsonObject = dict[str, typing.Any]

SECRET = types.ui.FieldType.PASSWORD
HIDDEN = types.ui.FieldType.HIDDEN


class ProviderUpdate(mutability_base.MutableActionType):
    """Proposal: update an existing service provider."""

    type_id = "provider.update"
    title = "Propose provider update"
    description = (
        "Propose changes to an existing service provider. The proposal does NOT "
        "apply anything: it is queued until an administrator approves it. "
        "Mutable fields are the same the REST PUT accepts (name, comments, tags "
        "and the provider type configuration fields); use get_mutable_fields to "
        "discover them and their current values."
    )
    handler = Providers

    # ------------------------------------------------- generic CAS layer

    @typing.override
    def flatten_values(self, values: JsonObject) -> JsonObject:
        """REST shape -> flat keyspace: ``instance.x`` for config fields."""
        flat = {k: v for k, v in values.items() if k != "instance"}
        instance = values.get("instance")
        if isinstance(instance, dict):
            for key, value in typing.cast("dict[str, typing.Any]", instance).items():
                flat[f"instance.{key}"] = value
        elif instance is not None:
            # Wrong shape on purpose: base validation rejects it as unknown
            # and lists the accepted instance.* names.
            flat["instance"] = instance
        return flat

    # ------------------------------------------------------------- hooks

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.Provider.objects.get(uuid__iexact=target_uuid)
        except models.Provider.DoesNotExist:
            raise rest_exceptions.NotFound("Provider not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return typing.cast(models.Provider, target).data_type

    @typing.override
    def field_definitions(self, for_type: str) -> list[JsonObject]:
        defs: list[JsonObject] = []
        for element in self._gui(for_type):
            info = element.gui
            definition: JsonObject = {
                "name": element.name,
                "type": info.type.value,
                "label": info.label,
                "tooltip": info.tooltip,
                "secret": info.type in (SECRET, HIDDEN),
                "from_instance": element.name.startswith("instance."),
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
        provider = typing.cast(models.Provider, target)
        instance_values = provider.get_instance(None).get_fields_as_dict()
        wanted = set(names)
        snapshot: JsonObject = {}
        for name in wanted:
            if name in instance_values:
                snapshot[name] = instance_values[name]
            elif name == "name":
                snapshot[name] = provider.name
            elif name == "comments":
                snapshot[name] = provider.comments
            elif name == "tags":
                snapshot[name] = sorted(t.tag for t in provider.tags.all())  # type: ignore[attr-defined]
        return snapshot

    @typing.override
    def etag_fields(self, for_type: str) -> list[str]:
        return [element.name for element in self._gui(for_type)]

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        provider = typing.cast(models.Provider, target)
        # Same sources the REST item serialization uses for these fields
        # (model columns + instance values under "instance"), so this
        # fingerprint tracks the handler's ETag inputs with the
        # deterministic ordering of item_etag.
        item_dict = {
            "name": provider.name,
            "comments": provider.comments,
            "tags": sorted(t.tag for t in provider.tags.all()),  # pyright: ignore[reportAttributeAccessIssue]
            "instance": provider.get_instance(None).get_fields_as_dict(),
        }
        return item_etag(item_dict, self.etag_fields(self.for_type_of(target)))

    @typing.override
    async def execute(self, action: "models.FlowAction", request: typing.Any) -> str:
        provider = typing.cast(models.Provider, self.resolve_target(action.target_uuid))
        await RestProxy().execute(
            RestTarget(
                Providers,
                "providers",
                types.rest.CustomMethodMethod.PUT,
                args=(action.target_uuid,),
            ),
            request,
            dict(action.values),
        )
        return f'Provider "{provider.name}" updated'

    # ------------------------------------------------------------ helpers

    def _gui(self, for_type: str) -> list[types.ui.GuiElement]:
        """Gui elements of one provider type, without the auth machinery.

        ``Providers.get_gui`` is request-independent; binding it to an
        empty namespace avoids instantiating the full handler (whose
        ``__init__`` resolves authentication).
        """
        shim = typing.cast(typing.Any, _Namespace())
        return sorted(Providers.get_gui(shim, for_type), key=lambda f: f.gui.order)
