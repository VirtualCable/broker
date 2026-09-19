"""``service_pool_group.update`` / ``.create`` / ``.delete``: the "Pool
Groups" shown to users (folders that classify service pools and meta
pools for display).

Replicates the REST ``PUT``/``POST``/``DELETE`` on ``/gallery``: ``name``,
``comments``, ``priority`` and ``image_id``. The image is a nullable
SET_NULL foreign key to an uploaded icon, exactly like the pool group of
a pool: the agent can select, change or clear it (``-1`` is the picker's
own "no image" option, ``""`` the form-wide no-selection sentinel), so it
is proposable on every verb.

Names are unique: a duplicate fails at execution with the REST's own
error, like any other create. The DELETE removes the row synchronously;
the pools and meta pools classified under it survive, they simply lose
the classification (``on_delete=SET_NULL``), so the verb keeps the strict
whole-item CAS.

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
from uds.REST.methods.services_pool_groups import ServicesPoolGroups
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.mcp.rest_proxy import RestProxy, RestTarget

from .. import base as mutability_base
from .. import gui_view
from .. import verbs as mutability_verbs
from ..base import ActionOperation
from ..etag import item_etag

JsonObject = dict[str, typing.Any]


class ServicePoolGroupUpdate(mutability_base.MutableActionType):
    """Proposal: create, update, or delete a service pool group."""

    type_id = "service_pool_group"
    title = "Propose service pool group update"
    description = (
        "Propose changes to an existing service pool group (a folder of service pools "
        "shown to users). The proposal does NOT apply anything: it is queued until an "
        "administrator approves it. Mutable fields are name, comments, priority and "
        "image_id (the icon picker; '-1' clears the icon); use get_mutable_fields to "
        "discover them and their current values."
    )
    handler = ServicesPoolGroups
    noun = "Service pool group"
    # A pool group is not a module: no subtype gallery, no data_type
    create_has_gallery = False

    # ------------------------------------------------------------- hooks

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.ServicePoolGroup.objects.get(uuid__iexact=target_uuid)
        except models.ServicePoolGroup.DoesNotExist:
            raise rest_exceptions.NotFound("Service pool group not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return "service_pool_group"

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        # Deletion needs no fields: the target itself is what disappears.
        if self.operation is ActionOperation.DELETE:
            return []
        if self.operation is ActionOperation.CREATE:
            return self.create_field_definitions(for_type, target)
        # The agent view is the whole handler gui: every field the form
        # carries is proposable (the image is a SET_NULL reference the
        # administrator can pick, change or clear)
        columns = frozenset(
            name for name, _modifier in ServicesPoolGroups.parse_save_fields(ServicesPoolGroups.FIELDS_TO_SAVE)
        )
        return gui_view.agent_definitions(
            self._gui_elements(for_type),
            from_instance=lambda name: name not in columns,
        )

    @typing.override
    def create_field_definitions(
        self,
        for_type: str,
        target: db_models.Model | None = None,
    ) -> list[JsonObject]:
        # The creation view of the same gui (no readonly/context fields to
        # unlock here; the image is proposable on every verb)
        return gui_view.agent_definitions(self._gui_elements(for_type), for_creation=True)

    @typing.override
    def create_validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        # Pool groups have no subtype gallery: the data_type is not
        # required. The base validation already checks the image choices
        # universe; the only thing it cannot enforce is that the name
        # actually carries text (names are unique).
        errors = super().create_validate_values(for_type or "service_pool_group", values, target)
        if not str(values.get("name", "")).strip():
            errors.append("field name: required")
        return errors

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        pool_group = typing.cast(models.ServicePoolGroup, target)
        wanted = set(names)
        # The image as the form-shaped PUT expects it ("-1" meaning none,
        # same convention the admin UI uses)
        values: JsonObject = {
            "name": pool_group.name,
            "comments": pool_group.comments,
            "priority": pool_group.priority,
            "image_id": pool_group.image.uuid if pool_group.image else "-1",
        }
        return {name: values[name] for name in wanted if name in values}

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        # Whole-item fingerprint: everything the form-shaped payload carries
        # (operation-independent: a delete-bound instance still fingerprints
        # the full item, while its proposal field surface is empty)
        return gui_view.fingerprint_names(self._gui_elements(for_type or "service_pool_group"))

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        fields = self.etag_fields("service_pool_group")
        item_dict: JsonObject = self.snapshot_values(target, fields)
        return item_etag(item_dict, fields)

    # ---------------------------------------------------------- helpers

    def _gui_elements(self, for_type: str) -> list[types.ui.GuiElement]:
        """The handler gui, ordered as the builder declared.

        ``ServicesPoolGroups.get_gui`` is request-independent; binding it
        to an empty namespace avoids instantiating the full handler (whose
        ``__init__`` resolves authentication).
        """
        shim = typing.cast(typing.Any, _Namespace())
        return sorted(ServicesPoolGroups.get_gui(shim, for_type), key=lambda element: element.gui.order)

    # ---------------------------------------------------------- execution

    @typing.override
    def tool_title(self) -> str:
        if self.operation is ActionOperation.CREATE:
            return mutability_verbs.create_tool_title("service pool group")
        if self.operation is ActionOperation.DELETE:
            return mutability_verbs.delete_tool_title("service pool group")
        return self.title

    @typing.override
    def tool_description(self) -> str:
        if self.operation is ActionOperation.CREATE:
            return mutability_verbs.create_tool_description(
                "service pool group",
                "name (required, unique), comments, priority and image_id (the icon picker; '-1' for no image)",
            )
        if self.operation is ActionOperation.DELETE:
            return mutability_verbs.delete_tool_description(
                "service pool group",
                "the REST removes it right away; the service pools and meta pools "
                "classified under it are NOT deleted, they simply lose the "
                "classification (the reference is set to null) — exactly like the "
                "administration interface.",
            )
        return self.description

    @typing.override
    async def op_create(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        values = dict(action.values)
        # The POST is form-shaped: every key of FIELDS_TO_SAVE rides,
        # omitted ones take the value the admin form sends on "new" (""
        # for comments, the gui default for priority, "-1" for the image
        # picker).
        params: JsonObject = {
            "name": values.get("name"),
            "comments": values.get("comments", ""),
            "image_id": values.get("image_id", "-1"),
            "priority": values.get("priority", 1),
        }
        new_uuid = await mutability_verbs.execute_create(
            RestTarget(ServicesPoolGroups, "gallery", types.rest.CustomMethodMethod.POST),
            request,
            params,
        )
        return mutability_verbs.created_message("Service pool group", str(params["name"]), new_uuid)

    @typing.override
    async def op_update(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The PUT is form-shaped (every FIELDS_TO_SAVE entry is required):
        # merge the proposal over the CAS-verified current values, so a
        # partial proposal applies instead of failing on approval. ALL the
        # ORM work must stay out of the async context.
        def _build_params() -> tuple[str, JsonObject]:
            pool_group = typing.cast(models.ServicePoolGroup, self.resolve_target(action.target_uuid))
            params = self.snapshot_values(pool_group, self.etag_fields("service_pool_group"))
            params.update(action.values)
            return pool_group.name, params

        pool_group_name, params = await sync_to_async(_build_params, thread_sensitive=True)()
        await RestProxy().execute(
            RestTarget(
                ServicesPoolGroups,
                "gallery",
                types.rest.CustomMethodMethod.PUT,
                args=(action.target_uuid,),
            ),
            request,
            params,
        )
        return f'Service pool group "{pool_group_name}" updated'

    @typing.override
    async def op_delete(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The REST DELETE removes the row synchronously; pools and meta
        # pools keep existing, their classification goes to null. ALL the
        # ORM work stays out of the async context.
        pool_group = typing.cast(
            models.ServicePoolGroup,
            await sync_to_async(self.resolve_target, thread_sensitive=True)(action.target_uuid),
        )
        await mutability_verbs.execute_delete(
            RestTarget(
                ServicesPoolGroups,
                "gallery",
                types.rest.CustomMethodMethod.DELETE,
                args=(action.target_uuid,),
            ),
            request,
        )
        return f'Service pool group "{pool_group.name}" deleted'
