"""``servicepool`` (create / update / delete): registration, discovery, CAS and execution."""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds.core import types
from uds.core.consts.mcp import CREATE_TARGET_UUID
from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.base import ActionOperation, StalePolicy
from uds.mutability.types.service_pools import ServicePoolUpdate
from uds.REST.methods.services_pools import ServicesPools

from tests.fixtures.services import (
    create_db_osmanager,
    create_db_provider,
    create_db_service,
    create_db_servicepool,
)
from tests.mcp.mutability._helpers import FlowTestCase, build_action, make_request

if typing.TYPE_CHECKING:
    from uds import models


def _create_pool() -> tuple["models.ServicePool", "models.Service", "models.OSManager"]:
    service = create_db_service(create_db_provider())
    osmanager = create_db_osmanager()
    return create_db_servicepool(service=service, osmanager=osmanager), service, osmanager


def _bound(operation: str) -> ServicePoolUpdate:
    factory = registry_get(f"servicepool.{operation}")
    assert factory is not None
    return typing.cast(ServicePoolUpdate, factory())


class ServicePoolUpdateRegistryTest(FlowTestCase):
    def test_all_three_operations_are_registered(self) -> None:
        found = registry_get("servicepool.update")
        assert found is not None
        self.assertIs(type(found()), ServicePoolUpdate)
        self.assertIs(type(_bound("create")), ServicePoolUpdate)
        self.assertIs(type(_bound("delete")), ServicePoolUpdate)
        self.assertIn("servicepool.update", all_type_ids())
        self.assertIn("servicepool.create", all_type_ids())
        self.assertIn("servicepool.delete", all_type_ids())
        self.assertEqual(
            ServicePoolUpdate.supported_operations(),
            frozenset({ActionOperation.UPDATE, ActionOperation.CREATE, ActionOperation.DELETE}),
        )

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            ServicePoolUpdate().resolve_target("00000000-0000-0000-0000-000000000000")

    def test_stale_policy_is_deny_for_update_and_force_for_delete(self) -> None:
        self.assertIs(_bound("update").get_stale_policy(), StalePolicy.DENY)
        self.assertIs(_bound("delete").get_stale_policy(), StalePolicy.FORCE)


class ServicePoolUpdateFieldsTest(FlowTestCase):
    def test_mutable_fields_and_excluded_references(self) -> None:
        # The handler gui refuses to build without at least one service
        create_db_service(create_db_provider())
        defs = ServicePoolUpdate().field_definitions("servicepool")
        names = [d["name"] for d in defs]

        for name in (
            "name",
            "short_name",
            "comments",
            "tags",
            "initial_srvs",
            "cache_l1_srvs",
            "cache_l2_srvs",
            "max_srvs",
            "show_transports",
            "visible",
            "allow_users_remove",
            "allow_users_reset",
            "ignores_unused",
            "calendar_message",
            "custom_message",
            "display_custom_message",
        ):
            self.assertIn(name, names)
        # The agent view is derived from the handler gui: the taglist is
        # a taglist, not the text the old hardcoded defs lied about
        by_name = {d["name"]: d for d in defs}
        self.assertEqual(by_name["tags"]["type"], "taglist")

        # Identity/reference columns are not proposable
        for name in (
            "service_id",
            "osmanager_id",
            "image_id",
            "pool_group_id",
            "account_id",
            "publish_on_save",
        ):
            self.assertNotIn(name, names)
        errors = ServicePoolUpdate().validate_values("servicepool", {"service_id": "other"})
        self.assertTrue(any("unknown fields" in e for e in errors))
        errors = ServicePoolUpdate().validate_values("servicepool", {"cache_l1_srvs": "soon"})
        self.assertTrue(any("must be numeric" in e for e in errors))

    def test_delete_publishes_no_fields(self) -> None:
        self.assertEqual(_bound("delete").field_definitions("servicepool"), [])
        self.assertEqual(_bound("delete").validate_values("servicepool", {}, None), [])
        self.assertIn("Propose deleting a service pool", _bound("delete").tool_title())
        self.assertIn("queued until an administrator approves", _bound("delete").tool_description())

    def test_snapshot_covers_columns_and_context(self) -> None:
        pool, service, osmanager = _create_pool()
        action_type = ServicePoolUpdate()

        snapshot = action_type.snapshot_values(pool, action_type.etag_fields("servicepool"))
        # Context uuids, as the PUT expects them
        self.assertEqual(snapshot["service_id"], service.uuid)
        self.assertEqual(snapshot["osmanager_id"], osmanager.uuid)
        self.assertEqual(snapshot["image_id"], "-1")
        self.assertEqual(snapshot["pool_group_id"], "-1")
        self.assertEqual(snapshot["account_id"], "")
        # Columns
        self.assertEqual(snapshot["cache_l1_srvs"], pool.cache_l1_srvs)
        self.assertEqual(snapshot["allow_users_remove"], pool.allow_users_remove)

        base = action_type.fingerprint(pool)
        self.assertEqual(base, action_type.fingerprint(pool))
        pool.comments = "changed"
        pool.save(update_fields=["comments"])
        self.assertNotEqual(base, action_type.fingerprint(pool))


class ServicePoolUpdateExecuteTest(FlowTestCase):
    def test_execute_sends_whole_form_with_proposed_values(self) -> None:
        pool, _service, _osmanager = _create_pool()
        action = self._action(
            self._flow(),
            action_type="servicepool.update",
            target_uuid=pool.uuid,
            values={"cache_l1_srvs": 7},
            base_values={"cache_l1_srvs": pool.cache_l1_srvs},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types.service_pools.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            summary = async_to_sync(_bound("update").execute)(action, request=make_request())

        self.assertIn("updated", summary)
        execute_call = proxy_cls.return_value.execute.call_args
        self.assertIs(execute_call[0][0].handler, ServicesPools)
        params: dict[str, typing.Any] = execute_call[0][2]
        # The PUT is form-shaped: every required field travels, proposed
        # values win, context keeps stored values
        for name in (
            "name",
            "short_name",
            "comments",
            "tags",
            "service_id",
            "osmanager_id",
            "image_id",
            "pool_group_id",
            "account_id",
            "initial_srvs",
            "cache_l1_srvs",
            "cache_l2_srvs",
            "max_srvs",
            "show_transports",
            "visible",
            "allow_users_remove",
            "allow_users_reset",
            "ignores_unused",
            "calendar_message",
            "custom_message",
            "display_custom_message",
        ):
            self.assertIn(name, params)
        self.assertEqual(params["cache_l1_srvs"], 7)
        self.assertEqual(params["service_id"], pool.service.uuid)
        osmanager = typing.cast("models.OSManager", pool.osmanager)
        self.assertEqual(params["osmanager_id"], osmanager.uuid)

    def test_delete_marks_the_pool_for_removal(self) -> None:
        pool, _service, _osmanager = _create_pool()
        action = self._action(
            self._flow(),
            action_type="servicepool.delete",
            target_uuid=pool.uuid,
            values={},
            base_values={},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types.service_pools.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="OK")
            summary = async_to_sync(_bound("delete").execute)(action, request=make_request())

        self.assertIn("queued for removal", summary)
        execute_call = proxy_cls.return_value.execute.call_args
        target = execute_call[0][0]
        self.assertIs(target.handler, ServicesPools)
        self.assertEqual(target.path, "services_pools")
        self.assertEqual(target.method, types.rest.CustomMethodMethod.DELETE)
        self.assertEqual(target.args, (pool.uuid,))
        self.assertEqual(target.parent, None)
        self.assertEqual(execute_call[1], {})

    def test_delete_aborts_when_the_pool_vanished(self) -> None:
        pool, _service, _osmanager = _create_pool()
        action = self._action(
            self._flow(),
            action_type="servicepool.delete",
            target_uuid=pool.uuid,
            values={},
            base_values={},
            base_etag="etag",
        )
        pool.delete()
        with self.assertRaises(rest_exceptions.NotFound):
            async_to_sync(_bound("delete").execute)(action, request=make_request())


class ServicePoolCreateTest(FlowTestCase):
    """servicepool.create: the creation view, capability validation, POST shape."""

    def test_creation_view_unlocks_the_references(self) -> None:
        service = create_db_service(create_db_provider())
        osmanager = create_db_osmanager()
        defs = _bound("create").create_field_definitions("servicepool")
        names = [d["name"] for d in defs]
        # References are selected on creation, so they join the surface...
        for name in ("service_id", "osmanager_id", "account_id", "image_id", "pool_group_id"):
            self.assertIn(name, names)
        # ...while publish_on_save (hidden: an operator convenience, never
        # an agent payload) and the plain update-only exclusions stay out
        self.assertNotIn("publish_on_save", names)
        self.assertNotIn("state", names)
        by_name = {d["name"]: d for d in defs}
        # The gui unlocks readonly fields on "new": the creation view
        # must not echo the flag (it would claim the agent cannot set them)
        self.assertNotIn("readonly", by_name["service_id"])
        # The live inventory is the choice universe at proposal time
        self.assertIn(service.uuid, [str(c["value"]) for c in by_name["service_id"]["choices"]])
        self.assertIn("-1", [str(c["value"]) for c in by_name["osmanager_id"]["choices"]])
        self.assertIn(osmanager.uuid, [str(c["value"]) for c in by_name["osmanager_id"]["choices"]])

    def test_create_requires_name_and_resolvable_service(self) -> None:
        service = create_db_service(create_db_provider())
        create = _bound("create")
        errors = create.create_validate_values("servicepool", {})
        self.assertTrue(any("name" in e for e in errors))
        self.assertTrue(any("service_id" in e for e in errors))
        # name + a service outside the live universe -> rejected
        self.assertTrue(
            any(
                "service_id" in e
                for e in create.create_validate_values("servicepool", {"name": "p", "service_id": "deadbeef"})
            )
        )
        # the minimal honest proposal validates clean
        self.assertEqual(
            create.create_validate_values("servicepool", {"name": "p", "service_id": service.uuid}), []
        )

    def test_create_checks_the_service_capabilities(self) -> None:
        cached = create_db_service(create_db_provider())  # TestServiceCache
        plain = create_db_service(create_db_provider(), use_caching_version=False)  # TestServiceNoCache
        osmanager = create_db_osmanager()
        create = _bound("create")
        # a service without cache must not carry cache sizes
        errors = create.create_validate_values(
            "servicepool", {"name": "p", "service_id": plain.uuid, "initial_srvs": 3}
        )
        self.assertTrue(any("initial_srvs" in e and "does not use cache" in e for e in errors))
        self.assertEqual(
            create.create_validate_values("servicepool", {"name": "p", "service_id": plain.uuid}), []
        )
        # max below initial/l1 is the silent clamp the proposal must not hide
        errors = create.create_validate_values(
            "servicepool",
            {"name": "p", "service_id": cached.uuid, "initial_srvs": 5, "max_srvs": 2},
        )
        self.assertTrue(any("max_srvs" in e for e in errors))
        # reset permission requires a service that can reset (it cannot)
        errors = create.create_validate_values(
            "servicepool", {"name": "p", "service_id": cached.uuid, "allow_users_reset": True}
        )
        self.assertTrue(any("allow_users_reset" in e for e in errors))
        # an OS manager for a service that needs none is refused
        errors = create.create_validate_values(
            "servicepool", {"name": "p", "service_id": cached.uuid, "osmanager_id": osmanager.uuid}
        )
        self.assertTrue(any("osmanager_id" in e and "does not need" in e for e in errors))

    def test_create_requires_matching_osmanager_when_needed(self) -> None:
        from tests.fixtures.modules.osmanager.testing_osmanager import TestOSManager
        from tests.fixtures.modules.service.service import TestServiceCache

        service = create_db_service(create_db_provider())
        osmanager = create_db_osmanager()
        create = _bound("create")
        with mock.patch.object(TestServiceCache, "needs_osmanager", True):
            # missing -> required
            errors = create.create_validate_values("servicepool", {"name": "p", "service_id": service.uuid})
            self.assertTrue(any("osmanager_id" in e and "required" in e for e in errors))
            # provided and type-matching -> clean
            self.assertEqual(
                create.create_validate_values(
                    "servicepool", {"name": "p", "service_id": service.uuid, "osmanager_id": osmanager.uuid}
                ),
                [],
            )
            # provided but managing a different service type -> refused (admin form parity)
            with mock.patch.object(TestOSManager, "services_types", types.services.ServiceType.VAPP):
                errors = create.create_validate_values(
                    "servicepool", {"name": "p", "service_id": service.uuid, "osmanager_id": osmanager.uuid}
                )
            self.assertTrue(any("does not manage the service type" in e for e in errors))

    def test_create_execution_posts_the_form_shape(self) -> None:
        service = create_db_service(create_db_provider())
        action = build_action(
            action_type="servicepool.create",
            target_uuid=CREATE_TARGET_UUID,
            values={"name": "new pool", "service_id": service.uuid, "cache_l1_srvs": 2, "max_srvs": 4},
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value={"id": "pool-uuid"})
            summary = async_to_sync(_bound("create").execute)(action, request=make_request())
            target, _request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual(
                (target.handler, target.path, target.method.value, target.args),
                (ServicesPools, "services_pools", "POST", ()),
            )
        # form-shaped: every required key of the POST rides
        self.assertEqual(params["name"], "new pool")
        self.assertEqual(params["service_id"], service.uuid)
        self.assertEqual(params["cache_l1_srvs"], 2)
        self.assertEqual(params["max_srvs"], 4)
        self.assertEqual(params["initial_srvs"], 0)
        self.assertEqual(params["image_id"], "-1")
        self.assertEqual(params["pool_group_id"], "-1")
        self.assertEqual(params["account_id"], "")
        self.assertEqual(params["osmanager_id"], "")
        self.assertTrue(params["visible"])
        self.assertFalse(params["allow_users_reset"])
        # publishing is its own operation: the key never rides, and the
        # handler's post_save default is False
        self.assertNotIn("publish_on_save", params)
        self.assertIn("created", summary)
        self.assertIn("pool-uuid", summary)

    def test_create_tool_texts(self) -> None:
        create = _bound("create")
        self.assertIn("Propose creating a service pool", create.tool_title())
        self.assertIn("service_id", create.tool_description())
