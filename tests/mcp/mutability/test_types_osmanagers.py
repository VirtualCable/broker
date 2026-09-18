"""``osmanager.update`` / ``osmanager.create`` / ``.delete``: registration, discovery, CAS and execution shape."""

from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.consts.mcp import CREATE_TARGET_UUID
from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.base import ActionOperation, StalePolicy
from uds.mutability.types.osmanagers import OsManagerUpdate
from uds.REST.methods.osmanagers import OsManagers

from tests.fixtures.services import create_db_osmanager, ensure_test_modules_registered
from tests.mcp.mutability._helpers import FlowTestCase, build_action, make_request

TEST_OSMANAGER_TYPE = "TestOsManager"


class OsManagerUpdateRegistryTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("osmanager.update")
        assert found is not None
        self.assertIs(type(found()), OsManagerUpdate)
        self.assertIn("osmanager.update", all_type_ids())

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            OsManagerUpdate().resolve_target("00000000-0000-0000-0000-000000000000")


class OsManagerUpdateFieldsTest(FlowTestCase):
    def test_field_definitions_include_module_fields(self) -> None:
        ensure_test_modules_registered()
        defs = OsManagerUpdate().field_definitions(TEST_OSMANAGER_TYPE)
        names = [d["name"] for d in defs]

        for name in ("name", "comments", "tags", "idle"):
            self.assertIn(name, names)

        # on_logout is readonly in the module gui: never mutable
        self.assertNotIn("on_logout", names)

        by_name = {d["name"]: d for d in defs}
        self.assertTrue(by_name["idle"]["from_instance"])
        self.assertFalse(by_name["name"]["from_instance"])

    def test_snapshot_and_fingerprint_track_model_and_instance(self) -> None:
        osmanager = create_db_osmanager()
        action_type = OsManagerUpdate()

        fields = action_type.etag_fields(action_type.for_type_of(osmanager))
        snapshot = action_type.snapshot_values(osmanager, fields)
        self.assertEqual(snapshot["name"], osmanager.name)
        self.assertIn("idle", snapshot)
        self.assertNotIn("on_logout", snapshot)

        base = action_type.fingerprint(osmanager)
        self.assertEqual(base, action_type.fingerprint(osmanager))

        osmanager.comments = "changed"
        osmanager.save(update_fields=["comments"])
        self.assertNotEqual(base, action_type.fingerprint(osmanager))


class OsManagerUpdateExecuteTest(FlowTestCase):
    def test_execute_merges_over_the_cas_verified_values(self) -> None:
        osmanager = create_db_osmanager()
        action = self._action(
            self._flow(),
            action_type="osmanager.update",
            target_uuid=osmanager.uuid,
            values={"idle": 600},
            base_values={"idle": 300},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types._module_update.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            # async_to_sync mirrors the production executor: the
            # thread-sensitive ORM work runs on the caller thread
            summary = async_to_sync(OsManagerUpdate().execute)(action, request=make_request())

        self.assertIn("updated", summary)
        execute_call = proxy_cls.return_value.execute.call_args
        self.assertIs(execute_call[0][0].handler, OsManagers)
        params = execute_call[0][2]
        for name in ("name", "comments", "tags", "idle"):
            self.assertIn(name, params)
        # Readonly module fields are neither proposable nor sent
        self.assertNotIn("on_logout", params)
        self.assertEqual(params["idle"], 600)
        self.assertEqual(params["data_type"], TEST_OSMANAGER_TYPE)


class OsManagerCreateDeleteTest(FlowTestCase):
    """osmanager.create / osmanager.delete: the module root verbs."""

    def test_supported_operations_derive_from_hooks(self) -> None:
        self.assertEqual(
            OsManagerUpdate.supported_operations(),
            frozenset({ActionOperation.UPDATE, ActionOperation.CREATE, ActionOperation.DELETE}),
        )

    def test_delete_rides_the_strict_deny_policy(self) -> None:
        delete = OsManagerUpdate(ActionOperation.DELETE)
        self.assertEqual(delete.full_id, "osmanager.delete")
        self.assertEqual(delete.get_stale_policy(), StalePolicy.DENY)

    def test_create_execution_posts_the_module_form_shape(self) -> None:
        # Registers the TestOsManager module in the factory (its gui is
        # needed to build the creation payload)
        ensure_test_modules_registered()
        create = OsManagerUpdate(ActionOperation.CREATE)
        action = build_action(
            action_type="osmanager.create",
            target_uuid=CREATE_TARGET_UUID,
            values={"data_type": TEST_OSMANAGER_TYPE, "name": "new osm", "idle": 600},
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value={"id": "osm-uuid"})
            summary = async_to_sync(create.execute)(action, request=make_request())
            target, _request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual((target.handler, target.method.value, target.args), (OsManagers, "POST", ()))
            # Form shape: the handler columns top-level, the module
            # configuration under instance, the subtype injected
            self.assertEqual(params["name"], "new osm")
            self.assertEqual(params["comments"], "")
            self.assertEqual(params["tags"], [])
            self.assertEqual(params["data_type"], TEST_OSMANAGER_TYPE)
            self.assertEqual(params["instance"], {"idle": 600})
        self.assertIn("new osm", summary)
        self.assertIn("osm-uuid", summary)

    def test_delete_execution_sends_canonical_delete(self) -> None:
        osmanager = create_db_osmanager()
        delete = OsManagerUpdate(ActionOperation.DELETE)
        action = build_action(
            action_type="osmanager.delete",
            target_uuid=osmanager.uuid,
            values={},
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock()
            summary = async_to_sync(delete.execute)(action, request=make_request())
            target, _request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual(
                (target.handler, target.method.value, target.args),
                (OsManagers, "DELETE", (osmanager.uuid,)),
            )
            self.assertEqual(params, {})
        self.assertIn(osmanager.name, summary)
        self.assertIn("deleted", summary)
