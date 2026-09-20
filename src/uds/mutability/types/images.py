"""``image.create`` / ``.delete``: the icon gallery.

Replicates the REST ``POST``/``DELETE`` on ``/gallery/images``: ``name``
(unique) and ``data`` (the base64 of the image itself). The gallery has
no ``get_gui`` (the admin client paints it from the table instead), so
the two-field surface is declared here directly, mirroring the handler's
``FIELDS_TO_SAVE``.

At execution the handler's ``pre_save`` feeds ``data`` to the model's
``image`` setter, which decodes the base64, resizes the image down to
256x256, stores the PNG plus a 48x48 thumbnail and fills the width and
height columns. Unparseable image bytes do NOT fail the save: the model
silently stores a transparent placeholder, so the base64 itself is
validated at proposal time (an agent fixing a typo is cheap, an
administrator approving an unreadable icon is not).

The DELETE unicons the items referencing the image: the service pools,
meta pools and pool groups pointing at it survive, they simply lose the
icon (``on_delete=SET_NULL`` on all three references), exactly what the
administration interface does.

Validation, serialization and execution reuse the very same handler
machinery.
"""

import base64
import binascii
import collections.abc
import typing

from asgiref.sync import sync_to_async
from django.db import models as db_models

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.mcp.rest_proxy import RestTarget
from uds.REST.methods.images import Images

from .. import base as mutability_base
from .. import verbs as mutability_verbs
from ..base import ActionOperation
from ..etag import item_etag

JsonObject = dict[str, typing.Any]

# The gallery surface: the handler has no get_gui, so its FIELDS_TO_SAVE
# declaration is the whole form (both entries required by fields_from_params)
_IMAGE_DEFINITIONS: typing.Final[list[JsonObject]] = [
    {
        "name": "name",
        "type": "text",
        "label": "Name",
        "tooltip": "Unique name of the image (the reference pools and groups pick it by)",
        "secret": False,
    },
    {
        "name": "data",
        "type": "text",
        "label": "Image data",
        "tooltip": ("The image itself, base64 encoded (PNG/JPEG/SVG; resized to 256x256 when stored)"),
        "secret": False,
    },
]


class ImageActions(mutability_base.MutableActionType):
    """Proposal: create or delete a gallery image."""

    type_id = "image"
    # The gallery is not a module: no subtype gallery, no data_type
    create_has_gallery = False
    handler = Images
    noun = "Image"
    title = "Propose creating a gallery image"
    description = (
        "Propose creating a new image in the gallery (an icon a pool, a meta pool "
        "or a pool group can then reference): name (required, unique) and data "
        "(required, the base64 of the image). The proposal does NOT upload anything: "
        "it is queued until an administrator approves it."
    )

    # ------------------------------------------------------------- hooks

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.Image.objects.get(uuid__iexact=target_uuid)
        except models.Image.DoesNotExist:
            raise rest_exceptions.NotFound("Image not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return "image"

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        # Deletion needs no fields: the target itself is what disappears
        # (the families referencing it drop the icon by themselves). The
        # family implements no update verb.
        if self.operation is ActionOperation.DELETE:
            return []
        return [dict(definition) for definition in _IMAGE_DEFINITIONS]

    @typing.override
    def create_field_definitions(
        self, for_type: str, target: db_models.Model | None = None
    ) -> list[JsonObject]:
        return [dict(definition) for definition in _IMAGE_DEFINITIONS]

    @typing.override
    def create_validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        # No data_type to declare (the family has no gallery); both form
        # fields are genuinely required and the base64 must decode, or
        # the handler would store an unreadable placeholder icon.
        errors = super().create_validate_values(for_type or "image", values, target)
        if not str(values.get("name", "")).strip():
            errors.append("field name: required")
        data = values.get("data")
        if not str(data or "").strip():
            errors.append("field data: required (base64 of the image)")
        elif not _is_valid_base64(str(data)):
            errors.append("field data: not valid base64")
        return errors

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        image = typing.cast(models.Image, target)
        wanted = set(names)
        snapshot: JsonObject = {}
        if "name" in wanted:
            snapshot["name"] = image.name
        if "data" in wanted:
            # The same shape the form carries and the setter accepts
            snapshot["data"] = image.data64
        return snapshot

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        # Operation-independent: a delete-bound instance still
        # fingerprints the whole item (name and pixels), while its
        # proposal field surface is empty
        return [str(definition["name"]) for definition in _IMAGE_DEFINITIONS]

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        fields = self.etag_fields("image")
        item_dict: JsonObject = self.snapshot_values(target, fields)
        return item_etag(item_dict, fields)

    # ---------------------------------------------------- standard texts

    @typing.override
    def tool_title(self) -> str:
        if self.operation is ActionOperation.CREATE:
            return mutability_verbs.create_tool_title("gallery image")
        return mutability_verbs.delete_tool_title("gallery image")

    @typing.override
    def tool_description(self) -> str:
        if self.operation is ActionOperation.CREATE:
            return mutability_verbs.create_tool_description(
                "gallery image",
                "name (required, unique) and data (required, the base64 of the image)",
                extra=(
                    "The image is resized to at most 256x256 when stored, and a thumbnail is generated from it."
                ),
            )
        return mutability_verbs.delete_tool_description(
            "gallery image",
            "the REST removes it from the gallery; the service pools, meta pools "
            "and pool groups using it as icon are NOT deleted, they simply lose "
            "the icon (the reference is set to null) — exactly like the "
            "administration interface.",
        )

    # ---------------------------------------------------------- execution

    @typing.override
    async def op_create(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        values = dict(action.values)
        # The POST is form-shaped: both FIELDS_TO_SAVE entries ride (the
        # handler's pre_save feeds ``data`` to the model's image setter)
        params: JsonObject = {
            "name": values.get("name"),
            "data": values.get("data"),
        }
        new_uuid = await mutability_verbs.execute_create(
            RestTarget(Images, "gallery", types.rest.CustomMethodMethod.POST),
            request,
            params,
        )
        return mutability_verbs.created_message("Image", str(params["name"]), new_uuid)

    @typing.override
    async def op_delete(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The REST DELETE removes the row; the items referencing it
        # survive with no icon (SET_NULL). ALL the ORM work stays out of
        # the async context.
        image = typing.cast(
            models.Image,
            await sync_to_async(self.resolve_target, thread_sensitive=True)(action.target_uuid),
        )
        await mutability_verbs.execute_delete(
            RestTarget(
                Images,
                "gallery",
                types.rest.CustomMethodMethod.DELETE,
                args=(action.target_uuid,),
            ),
            request,
        )
        return f'Image "{image.name}" deleted'


def _is_valid_base64(value: str) -> bool:
    """True when ``value`` decodes as base64.

    The lenient check (no alphabet validation) mirrors what the model's
    ``image`` setter does with the string it receives: bytes that decode
    but are not an image are stored as the transparent placeholder, so
    only undecodable payloads are refused at proposal time.
    """
    try:
        base64.b64decode(value, validate=False)
    except (binascii.Error, ValueError):
        return False
    return True
