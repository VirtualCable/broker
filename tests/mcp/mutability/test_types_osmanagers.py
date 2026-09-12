"""``osmanager.update``: registration, discovery, CAS and execution shape."""

from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_types, get as registry_get
from uds.mutability.types.osmanagers import OsManagerUpdate
from uds.REST.methods.osmanagers import OsManagers

from tests.fixtures.services import create_db_osmanager, ensure_test_modules_registered
from tests.mcp.mutability._helpers import FlowTestCase, make_request

TEST_OSMANAGER_TYPE = "TestOsManager"


class OsManagerUpdateRegistryTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("osmanager.update")
        self.assertIs(found, OsManagerUpdate)
        self.assertIn("osmanager.update", [t.type_id for t in all_types()])

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
