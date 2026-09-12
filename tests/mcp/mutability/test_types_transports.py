"""``transport.update``: registration, discovery, CAS and execution shape."""

from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_types, get as registry_get
from uds.mutability.types.transports import TransportUpdate
from uds.REST.methods.transports import Transports

from tests.fixtures.services import create_db_transport, ensure_test_modules_registered
from tests.mcp.mutability._helpers import FlowTestCase, make_request

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false

TEST_TRANSPORT_TYPE = "TestTransport"


class TransportUpdateRegistryTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("transport.update")
        self.assertIs(found, TransportUpdate)
        self.assertIn("transport.update", [t.type_id for t in all_types()])

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            TransportUpdate().resolve_target("00000000-0000-0000-0000-000000000000")


class TransportUpdateFieldsTest(FlowTestCase):
    def test_field_definitions_exclude_m2m_and_add_module_fields(self) -> None:
        ensure_test_modules_registered()
        defs = TransportUpdate().field_definitions(TEST_TRANSPORT_TYPE)
        names = [d["name"] for d in defs]

        # Model fields (FIELDS_TO_SAVE) plus the module gui fields...
        for name in ("name", "comments", "tags", "priority", "label", "net_filtering", "allowed_oss"):
            self.assertIn(name, names)
        # ...the TestTransport module configuration fields...
        self.assertIn("test_url", names)
        self.assertIn("pin", names)
        # ...but never the m2m relations
        self.assertNotIn("networks", names)
        self.assertNotIn("pools", names)

        by_name = {d["name"]: d for d in defs}
        # Secrets are flagged by field type
        self.assertTrue(by_name["pin"]["secret"])
        self.assertFalse(by_name["test_url"]["secret"])
        # net_filtering is the stock choice (n/a/d)
        self.assertEqual(
            [c.id for c in by_name["net_filtering"]["choices"]],
            ["n", "a", "d"],
        )
        # Module fields are marked as instance configuration
        self.assertTrue(by_name["test_url"]["from_instance"])
        self.assertFalse(by_name["name"]["from_instance"])

    def test_snapshot_and_fingerprint_track_model_and_instance(self) -> None:
        ensure_test_modules_registered()
        transport = create_db_transport()
        action_type = TransportUpdate()
        for_type = action_type.for_type_of(transport)
        self.assertEqual(for_type, TEST_TRANSPORT_TYPE)

        fields = action_type.etag_fields(for_type)
        snapshot = action_type.snapshot_values(transport, fields)
        self.assertEqual(snapshot["name"], transport.name)
        self.assertEqual(snapshot["allowed_oss"], [])
        self.assertIn("test_url", snapshot)

        base = action_type.fingerprint(transport)
        self.assertEqual(base, action_type.fingerprint(transport))

        transport.priority = 3
        transport.save(update_fields=["priority"])
        self.assertNotEqual(base, action_type.fingerprint(transport))


class TransportUpdateExecuteTest(FlowTestCase):
    def test_execute_merges_over_the_cas_verified_values(self) -> None:
        ensure_test_modules_registered()
        transport = create_db_transport()
        action = self._action(
            self._flow(),
            action_type="transport.update",
            target_uuid=transport.uuid,
            values={"priority": 7},
            base_values={"priority": transport.priority},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types._module_update.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            # async_to_sync mirrors the production executor: the
            # thread-sensitive ORM work runs on the caller thread
            summary = async_to_sync(TransportUpdate().execute)(action, request=make_request())

        self.assertIn("updated", summary)
        execute_call = proxy_cls.return_value.execute.call_args
        self.assertIs(execute_call[0][0].handler, Transports)
        params = execute_call[0][2]
        # The PUT is form-shaped: every required field travels
        for name in ("name", "comments", "tags", "priority", "label", "net_filtering", "allowed_oss"):
            self.assertIn(name, params)
        # The proposed value wins; data_type comes from the target
        self.assertEqual(params["priority"], 7)
        self.assertEqual(params["data_type"], TEST_TRANSPORT_TYPE)
