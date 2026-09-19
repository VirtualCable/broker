"""``transport.update`` / ``transport.create`` / ``transport.delete``:
registration, discovery, CAS and execution shape."""

from unittest import mock

from asgiref.sync import async_to_sync

from uds import models
from uds.core.consts.mcp import CREATE_TARGET_UUID
from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.base import ActionOperation, StalePolicy
from uds.mutability.types.transports import TransportUpdate
from uds.REST.methods.transports import Transports

from tests.fixtures.services import create_db_transport, ensure_test_modules_registered
from tests.mcp.mutability._helpers import FlowTestCase, build_action, make_request

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false

TEST_TRANSPORT_TYPE = "TestTransport"


class TransportUpdateRegistryTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("transport.update")
        assert found is not None
        self.assertIs(type(found()), TransportUpdate)
        self.assertIn("transport.update", all_type_ids())

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            TransportUpdate().resolve_target("00000000-0000-0000-0000-000000000000")


class TransportUpdateFieldsTest(FlowTestCase):
    def test_field_definitions_cover_m2m_and_module_fields(self) -> None:
        ensure_test_modules_registered()
        defs = TransportUpdate().field_definitions(TEST_TRANSPORT_TYPE)
        names = [d["name"] for d in defs]

        # Model fields (FIELDS_TO_SAVE) plus the module gui fields...
        for name in ("name", "comments", "tags", "priority", "label", "net_filtering", "allowed_oss"):
            self.assertIn(name, names)
        # ...the TestTransport module configuration fields...
        self.assertIn("test_url", names)
        self.assertIn("pin", names)
        # ...and the m2m relations (part of the PUT form, like the admin
        # form sends them)
        self.assertIn("networks", names)
        self.assertIn("pools", names)

        by_name = {d["name"]: d for d in defs}
        # Secrets are flagged by field type
        self.assertTrue(by_name["pin"]["secret"])
        self.assertFalse(by_name["test_url"]["secret"])
        # net_filtering is the stock choice (n/a/d)
        self.assertEqual(
            [c["value"] for c in by_name["net_filtering"]["choices"]],
            ["n", "a", "d"],
        )
        # Module fields are marked as instance configuration; the model
        # columns are not. The m2m relations resolve through their own
        # snapshot path (they ride params), so they are marked like the
        # authenticator "networks" is, not as plain columns
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

    def test_relations_snapshot_as_sorted_uuid_lists(self) -> None:
        from tests.fixtures.networks import create_network

        ensure_test_modules_registered()
        transport = create_db_transport()
        action_type = TransportUpdate()

        # Empty relations snapshot as empty lists (the PUT shape)
        self.assertEqual(
            action_type.snapshot_values(transport, ["networks", "pools"]), {"networks": [], "pools": []}
        )

        network = create_network()
        transport.networks.add(network)
        self.assertEqual(
            action_type.snapshot_values(transport, ["networks"])["networks"],
            [network.uuid],
        )

        # relation drift cuts the fingerprint (the whole surface is covered)
        base = action_type.fingerprint(transport)
        other = models.Network.objects.create(name="Second net", net_string="10.9.0.0/16")
        transport.networks.add(other)
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
        # The m2m relations ride the merged PUT (current values: the
        # proposal omitted them); the merged PUT needs no "instance" key
        self.assertEqual(params["networks"], [])
        self.assertEqual(params["pools"], [])


class TransportCreateDeleteTest(FlowTestCase):
    """transport.create / transport.delete: the module root verbs."""

    def test_supported_operations_derive_from_hooks(self) -> None:
        self.assertEqual(
            TransportUpdate.supported_operations(),
            frozenset({ActionOperation.UPDATE, ActionOperation.CREATE, ActionOperation.DELETE}),
        )

    def test_delete_rides_the_strict_deny_policy(self) -> None:
        delete = TransportUpdate(ActionOperation.DELETE)
        self.assertEqual(delete.full_id, "transport.delete")
        self.assertEqual(delete.get_stale_policy(), StalePolicy.DENY)

    def test_create_execution_posts_the_module_form_shape(self) -> None:
        # Registers the TestTransport module in the factory (its gui is
        # needed to build the creation payload)
        ensure_test_modules_registered()
        create = TransportUpdate(ActionOperation.CREATE)
        action = build_action(
            action_type="transport.create",
            target_uuid=CREATE_TARGET_UUID,
            values={"data_type": TEST_TRANSPORT_TYPE, "name": "new transport", "test_url": "http://x"},
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value={"id": "tr-uuid"})
            summary = async_to_sync(create.execute)(action, request=make_request())
            target, _request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual((target.handler, target.method.value, target.args), (Transports, "POST", ()))
            # Form shape: columns top level, module config under instance
            self.assertEqual(params["name"], "new transport")
            self.assertEqual(params["comments"], "")
            self.assertEqual(params["tags"], [])
            self.assertEqual(params["data_type"], TEST_TRANSPORT_TYPE)
            self.assertEqual(params["instance"], {"test_url": "http://x"})
            # Declared gui defaults fill the omitted columns (priority,
            # net_filtering); the form-always columns use the declared
            # create defaults (allowed_oss, label)
            self.assertEqual(params["priority"], 1)
            self.assertEqual(params["net_filtering"], "n")
            self.assertEqual(params["allowed_oss"], [])
            self.assertEqual(params["label"], "")
            # The relations ALWAYS travel (the omitted ones as the empty
            # multiselect the gui sends, keeping both processed)
            self.assertEqual(params["networks"], [])
            self.assertEqual(params["pools"], [])
        self.assertIn("new transport", summary)
        self.assertIn("tr-uuid", summary)

    def test_create_execution_carries_relations_in_params(self) -> None:
        ensure_test_modules_registered()
        create = TransportUpdate(ActionOperation.CREATE)
        action = build_action(
            action_type="transport.create",
            target_uuid=CREATE_TARGET_UUID,
            values={
                "data_type": TEST_TRANSPORT_TYPE,
                "name": "bound transport",
                "networks": ["net-uuid-1"],
                "pools": ["pool-uuid-1"],
            },
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value={"id": "tr-uuid"})
            async_to_sync(create.execute)(action, request=make_request())
            _target, _request, params = proxy_cls.return_value.execute.call_args[0]
            # relations travel as params (post_save reads them there),
            # never as module configuration
            self.assertEqual(params["networks"], ["net-uuid-1"])
            self.assertEqual(params["pools"], ["pool-uuid-1"])
            self.assertNotIn("networks", params["instance"])
            self.assertNotIn("pools", params["instance"])

    def test_delete_execution_sends_canonical_delete(self) -> None:
        ensure_test_modules_registered()
        transport = create_db_transport()
        delete = TransportUpdate(ActionOperation.DELETE)
        action = build_action(
            action_type="transport.delete",
            target_uuid=transport.uuid,
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
                (Transports, "DELETE", (transport.uuid,)),
            )
            self.assertEqual(params, {})
        self.assertIn(transport.name, summary)
        self.assertIn("deleted", summary)
