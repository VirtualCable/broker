"""``image`` (create / delete): registration, validation, CAS and execution shapes."""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.consts.mcp import CREATE_TARGET_UUID
from uds.core.exceptions import rest as rest_exceptions
from uds.core.util.model import sql_now
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.base import ActionOperation
from uds.mutability.types.images import ImageActions
from uds.models import Image
from uds.REST.methods.images import Images

from tests.mcp.mutability._helpers import FlowTestCase, build_action, make_request

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false

# 1x1 transparent PNG: real image bytes, small enough for the proposal
_ONE_PIXEL_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


def _bound(operation: str) -> ImageActions:
    factory = registry_get(f"image.{operation}")
    assert factory is not None
    return typing.cast(ImageActions, factory())


class ImageRegistryTest(FlowTestCase):
    def test_the_two_operations_are_registered(self) -> None:
        self.assertIs(type(_bound("create")), ImageActions)
        self.assertIs(type(_bound("delete")), ImageActions)
        for verb in ("create", "delete"):
            self.assertIn(f"image.{verb}", all_type_ids())
        # No update verb: replacing an icon is uploading a new one
        self.assertEqual(
            ImageActions.supported_operations(),
            frozenset({ActionOperation.CREATE, ActionOperation.DELETE}),
        )

    def test_delete_publishes_no_fields(self) -> None:
        delete = _bound("delete")
        self.assertEqual(delete.field_definitions("image"), [])
        self.assertEqual(delete.validate_values("image", {}, None), [])
        self.assertIn("Propose deleting a gallery image", delete.tool_title())
        self.assertIn("lose the icon", delete.tool_description())

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            ImageActions().resolve_target("00000000-0000-0000-0000-000000000000")


class ImageCreateTest(FlowTestCase):
    """image.create: the surface, the base64 validation, the POST shape."""

    def test_creation_view_offers_the_form_fields(self) -> None:
        names = [d["name"] for d in _bound("create").create_field_definitions("image")]
        self.assertEqual(names, ["name", "data"])

    def test_create_requires_both_fields_and_valid_base64(self) -> None:
        create = _bound("create")
        errors = create.create_validate_values("image", {})
        self.assertTrue(any("name" in e for e in errors))
        self.assertTrue(any("data" in e for e in errors))
        self.assertTrue(
            any(
                "name" in e
                for e in create.create_validate_values("image", {"name": "  ", "data": _ONE_PIXEL_PNG})
            )
        )
        self.assertTrue(
            any("data" in e for e in create.create_validate_values("image", {"name": "icon", "data": "   "}))
        )
        # undecodable payloads would store an unreadable placeholder icon
        self.assertTrue(
            any(
                "base64" in e
                for e in create.create_validate_values("image", {"name": "icon", "data": "not base64!!"})
            )
        )
        self.assertEqual(create.create_validate_values("image", {"name": "icon", "data": _ONE_PIXEL_PNG}), [])

    def test_create_execution_posts_the_form_shape(self) -> None:
        action = build_action(
            action_type="image.create",
            target_uuid=CREATE_TARGET_UUID,
            values={"name": "icon", "data": _ONE_PIXEL_PNG},
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value={"id": "img-uuid"})
            summary = async_to_sync(_bound("create").execute)(action, request=make_request())
            target, _request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual(
                (target.handler, target.path, target.method.value, target.args),
                (Images, "gallery", "POST", ()),
            )
        self.assertEqual(params, {"name": "icon", "data": _ONE_PIXEL_PNG})
        self.assertIn("created", summary)
        self.assertIn("img-uuid", summary)


class ImageDeleteTest(FlowTestCase):
    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.image = Image(name="icon", stamp=sql_now())
        self.image.image = _ONE_PIXEL_PNG  # the very path the REST save uses
        self.image.save()

    def test_snapshot_and_fingerprint_cover_name_and_pixels(self) -> None:
        action_type = _bound("delete")
        snapshot = action_type.snapshot_values(self.image, ["name", "data"])
        self.assertEqual(snapshot["name"], "icon")
        self.assertEqual(snapshot["data"], self.image.data64)

        base = action_type.fingerprint(self.image)
        self.image.name = "renamed"
        self.image.save(update_fields=["name"])
        self.assertNotEqual(base, action_type.fingerprint(self.image))

    def test_delete_executes_the_rest_call(self) -> None:
        action = self._action(
            self._flow(),
            action_type="image.delete",
            target_uuid=self.image.uuid,
            values={},
            base_values={},
            base_etag="etag",
        )
        with mock.patch("uds.mutability.verbs.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            summary = async_to_sync(_bound("delete").execute)(action, request=make_request())
            target, _request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual(
                (target.handler, target.path, target.method.value, target.args),
                (Images, "gallery", "DELETE", (self.image.uuid,)),
            )
            self.assertEqual(params, {})
        self.assertIn("deleted", summary)
        self.assertIn("icon", summary)

    def test_delete_missing_target_is_not_found(self) -> None:
        action = self._action(
            self._flow(),
            action_type="image.delete",
            target_uuid="00000000-0000-0000-0000-000000000000",
            values={},
            base_values={},
            base_etag="etag",
        )
        with self.assertRaises(rest_exceptions.NotFound):
            async_to_sync(_bound("delete").execute)(action, request=make_request())
