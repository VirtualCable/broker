"""``service_pool_group`` (create / update / delete): registration, discovery, CAS, execution."""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.consts.mcp import CREATE_TARGET_UUID
from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.base import ActionOperation, StalePolicy
from uds.mutability.types.pool_groups import ServicePoolGroupUpdate
from uds.models import Image, ServicePoolGroup
from uds.REST.methods.services_pool_groups import ServicesPoolGroups

from tests.mcp.mutability._helpers import FlowTestCase, build_action, make_request

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


def _bound(operation: str) -> ServicePoolGroupUpdate:
    factory = registry_get(f"service_pool_group.{operation}")
    assert factory is not None
    return typing.cast(ServicePoolGroupUpdate, factory())


class ServicePoolGroupRegistryTest(FlowTestCase):
    def test_the_three_operations_are_registered(self) -> None:
        found = registry_get("service_pool_group.update")
        assert found is not None
        self.assertIs(type(found()), ServicePoolGroupUpdate)
        self.assertIs(type(_bound("create")), ServicePoolGroupUpdate)
        self.assertIs(type(_bound("delete")), ServicePoolGroupUpdate)
        for verb in ("update", "create", "delete"):
            self.assertIn(f"service_pool_group.{verb}", all_type_ids())
        self.assertEqual(
            ServicePoolGroupUpdate.supported_operations(),
            frozenset({ActionOperation.UPDATE, ActionOperation.CREATE, ActionOperation.DELETE}),
        )

    def test_delete_keeps_the_strict_cas(self) -> None:
        # The pool group row disappears synchronously (pools only lose the
        # classification): drift denies the flow, like on update
        self.assertIs(_bound("update").get_stale_policy(), StalePolicy.DENY)
        self.assertIs(_bound("delete").get_stale_policy(), StalePolicy.DENY)


class ServicePoolGroupFieldsTest(FlowTestCase):
    def test_the_image_is_proposable_like_the_plain_fields(self) -> None:
        names = [d["name"] for d in ServicePoolGroupUpdate().field_definitions("service_pool_group")]
        self.assertEqual(sorted(names), ["comments", "image_id", "name", "priority"])
        by_name = {d["name"]: d for d in ServicePoolGroupUpdate().field_definitions("service_pool_group")}
        self.assertEqual(by_name["image_id"]["type"], "imgchoice")
        # the picker's own "no image" option reaches the agent universe
        self.assertIn("-1", [str(c["value"]) for c in by_name["image_id"]["choices"]])

    def test_delete_publishes_no_fields(self) -> None:
        self.assertEqual(_bound("delete").field_definitions("service_pool_group"), [])
        self.assertEqual(_bound("delete").validate_values("service_pool_group", {}, None), [])
        self.assertIn("Propose deleting a service pool group", _bound("delete").tool_title())
        self.assertIn("lose the", _bound("delete").tool_description())

    def test_snapshot_and_fingerprint_track_priority_and_image(self) -> None:
        pool_group = ServicePoolGroup.objects.create(name="lab", priority=5)
        action_type = _bound("update")

        snapshot = action_type.snapshot_values(pool_group, ["name", "priority", "image_id"])
        self.assertEqual(snapshot, {"name": "lab", "priority": 5, "image_id": "-1"})

        base = action_type.fingerprint(pool_group)
        pool_group.priority = 1
        pool_group.save(update_fields=["priority"])
        self.assertNotEqual(base, action_type.fingerprint(pool_group))

        image = Image.objects.create(name="icon", data=b"", thumb=b"")
        pool_group.image = image
        pool_group.save(update_fields=["image"])
        self.assertNotEqual(action_type.fingerprint(pool_group), base)
        snapshot = action_type.snapshot_values(pool_group, ["image_id"])
        self.assertEqual(snapshot, {"image_id": image.uuid})

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            ServicePoolGroupUpdate().resolve_target("00000000-0000-0000-0000-000000000000")


class ServicePoolGroupUpdateExecuteTest(FlowTestCase):
    def test_execute_sends_rest_put_shape(self) -> None:
        pool_group = ServicePoolGroup.objects.create(name="lab", priority=5)
        action = self._action(
            self._flow(),
            action_type="service_pool_group.update",
            target_uuid=pool_group.uuid,
            values={"priority": 1},
            base_values={"priority": 5},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types.pool_groups.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            # async_to_sync mirrors the production executor: the
            # thread-sensitive ORM work runs on the caller thread
            summary = async_to_sync(_bound("update").execute)(action, request=make_request())

        self.assertIn("updated", summary)
        execute_call = proxy_cls.return_value.execute.call_args
        self.assertIs(execute_call[0][0].handler, ServicesPoolGroups)
        # The collection is "gallery" (the REST path of pool groups)
        self.assertEqual(execute_call[0][0].path, "gallery")
        # The PUT is form-shaped (image_id is a required save field): the
        # stored image travels as "-1" (none) unless the proposal touches it
        self.assertEqual(
            execute_call[0][2],
            {"name": "lab", "comments": "", "priority": 1, "image_id": "-1"},
        )

    def test_execute_replaces_the_image(self) -> None:
        pool_group = ServicePoolGroup.objects.create(name="lab", priority=5)
        action = self._action(
            self._flow(),
            action_type="service_pool_group.update",
            target_uuid=pool_group.uuid,
            values={"image_id": "some-image-uuid"},
            base_values={"image_id": "-1"},
            base_etag="etag",
        )
        with mock.patch("uds.mutability.types.pool_groups.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            async_to_sync(_bound("update").execute)(action, request=make_request())
        self.assertEqual(proxy_cls.return_value.execute.call_args[0][2]["image_id"], "some-image-uuid")


class ServicePoolGroupCreateTest(FlowTestCase):
    """service_pool_group.create: the surface, validation, POST shape."""

    def test_creation_view_offers_the_same_fields(self) -> None:
        names = [d["name"] for d in _bound("create").create_field_definitions("service_pool_group")]
        self.assertEqual(sorted(names), ["comments", "image_id", "name", "priority"])

    def test_create_requires_name(self) -> None:
        create = _bound("create")
        errors = create.create_validate_values("service_pool_group", {})
        self.assertTrue(any("name" in e for e in errors))
        self.assertEqual(create.create_validate_values("service_pool_group", {"name": "lab"}), [])
        # an image outside the picker universe (incl. invented uuids: the
        # universe is the live inventory) is refused
        self.assertTrue(
            any(
                "image_id" in e
                for e in create.create_validate_values("service_pool_group", {"name": "lab", "image_id": "bad"})
            )
        )

    def test_create_execution_posts_the_form_shape(self) -> None:
        action = build_action(
            action_type="service_pool_group.create",
            target_uuid=CREATE_TARGET_UUID,
            values={"name": "new group", "priority": 3},
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value={"id": "group-uuid"})
            summary = async_to_sync(_bound("create").execute)(action, request=make_request())
            target, _request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual(
                (target.handler, target.path, target.method.value, target.args),
                (ServicesPoolGroups, "gallery", "POST", ()),
            )
        self.assertEqual(params["name"], "new group")
        self.assertEqual(params["priority"], 3)
        # omitted form fields ride the admin "new" defaults
        self.assertEqual(params["comments"], "")
        self.assertEqual(params["image_id"], "-1")
        self.assertIn("created", summary)
        self.assertIn("group-uuid", summary)


class ServicePoolGroupDeleteTest(FlowTestCase):
    def test_delete_executes_the_rest_call(self) -> None:
        pool_group = ServicePoolGroup.objects.create(name="lab", priority=5)
        action = self._action(
            self._flow(),
            action_type="service_pool_group.delete",
            target_uuid=pool_group.uuid,
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
                (ServicesPoolGroups, "gallery", "DELETE", (pool_group.uuid,)),
            )
            self.assertEqual(params, {})
        self.assertIn("deleted", summary)
        self.assertIn("lab", summary)

    def test_delete_missing_target_is_not_found(self) -> None:
        action = self._action(
            self._flow(),
            action_type="service_pool_group.delete",
            target_uuid="00000000-0000-0000-0000-000000000000",
            values={},
            base_values={},
            base_etag="etag",
        )
        with self.assertRaises(rest_exceptions.NotFound):
            async_to_sync(_bound("delete").execute)(action, request=make_request())
