"""``network.update`` / ``.create`` / ``.delete``: registration, discovery, CAS and execution shape."""

from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.consts.mcp import CREATE_TARGET_UUID
from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.base import ActionOperation, StalePolicy
from uds.mutability.types.networks import NetworkUpdate
from uds.models import Network
from uds.REST.methods.networks import Networks

from tests.mcp.mutability._helpers import FlowTestCase, build_action, make_request

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


class NetworkUpdateRegistryTest(FlowTestCase):
    def test_network_update_is_registered(self) -> None:
        found = registry_get("network.update")
        assert found is not None
        self.assertIs(type(found()), NetworkUpdate)
        self.assertIn("network.update", all_type_ids())

    def test_supported_operations_derive_from_hooks(self) -> None:
        self.assertEqual(
            NetworkUpdate.supported_operations(),
            frozenset({ActionOperation.UPDATE, ActionOperation.CREATE, ActionOperation.DELETE}),
        )

    def test_delete_rides_the_strict_deny_policy(self) -> None:
        delete = NetworkUpdate(ActionOperation.DELETE)
        self.assertEqual(delete.full_id, "network.delete")
        self.assertEqual(delete.get_stale_policy(), StalePolicy.DENY)

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            NetworkUpdate().resolve_target("00000000-0000-0000-0000-000000000000")


class NetworkUpdateFieldsTest(FlowTestCase):
    def test_field_definitions_match_what_the_handler_saves(self) -> None:
        names = [d["name"] for d in NetworkUpdate().field_definitions("network")]
        # The network model has no comments column and its gui does not
        # offer the stock comments field either, so the proposable surface
        # is exactly the handler's FIELDS_TO_SAVE
        self.assertEqual(sorted(names), ["name", "net_string", "tags"])

    def test_snapshot_and_fingerprint_track_the_range(self) -> None:
        network = Network.objects.create(name="net1", net_string="10.0.0.0/24")
        action_type = NetworkUpdate()

        self.assertEqual(
            action_type.snapshot_values(network, ["name", "net_string"]),
            {"name": "net1", "net_string": "10.0.0.0/24"},
        )
        base = action_type.fingerprint(network)
        self.assertEqual(base, action_type.fingerprint(network))

        network.net_string = "192.168.0.0/16"
        network.save(update_fields=["net_string"])
        self.assertNotEqual(base, action_type.fingerprint(network))

    def test_update_validates_the_range_at_propose_time(self) -> None:
        # The model save would silently zero an unparseable range; the
        # proposal checks it the way the gui form does
        self.assertTrue(
            any(
                "net_string" in e
                for e in NetworkUpdate().validate_values("network", {"net_string": "not a range!!"})
            )
        )
        self.assertEqual(NetworkUpdate().validate_values("network", {"net_string": "10.0.0.1-10.0.0.10"}), [])


class NetworkCreateDeleteTest(FlowTestCase):
    """network.create / network.delete: the root verbs."""

    def test_create_requires_name_and_valid_range(self) -> None:
        create = NetworkUpdate(ActionOperation.CREATE)
        errors = create.create_validate_values("network", {})
        self.assertTrue(any("name" in e for e in errors))
        self.assertTrue(any("net_string" in e for e in errors))
        # A valid range (and no data_type: networks have no gallery)
        self.assertEqual(
            create.create_validate_values("network", {"name": "n", "net_string": "10.0.0.0/24"}), []
        )
        # ...an invalid one does not
        self.assertTrue(
            any(
                "net_string" in e
                for e in create.create_validate_values("network", {"name": "n", "net_string": "!!!"})
            )
        )

    def test_create_execution_posts_the_form_shape(self) -> None:
        create = NetworkUpdate(ActionOperation.CREATE)
        action = build_action(
            action_type="network.create",
            target_uuid=CREATE_TARGET_UUID,
            values={"name": "new net", "net_string": "10.0.0.0/24", "tags": ["a"]},
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value={"id": "net-uuid"})
            summary = async_to_sync(create.execute)(action, request=make_request())
            target, _request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual((target.handler, target.method.value, target.args), (Networks, "POST", ()))
            self.assertEqual(params, {"name": "new net", "net_string": "10.0.0.0/24", "tags": ["a"]})
        self.assertIn("new net", summary)
        self.assertIn("net-uuid", summary)

    def test_create_omitted_tags_ride_empty(self) -> None:
        create = NetworkUpdate(ActionOperation.CREATE)
        action = build_action(
            action_type="network.create",
            target_uuid=CREATE_TARGET_UUID,
            values={"name": "new net", "net_string": "10.0.0.0/24"},
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value=None)
            summary = async_to_sync(create.execute)(action, request=make_request())
            _target, _request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual(params["tags"], [])
        self.assertIn("created", summary)
        # no uuid reported -> the message says so (no parenthetical)
        self.assertNotIn("uuid", summary)

    def test_delete_execution_sends_canonical_delete(self) -> None:
        network = Network.objects.create(name="net-del", net_string="10.0.0.0/24")
        delete = NetworkUpdate(ActionOperation.DELETE)
        action = build_action(
            action_type="network.delete",
            target_uuid=network.uuid,
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
                (Networks, "DELETE", (network.uuid,)),
            )
            self.assertEqual(params, {})
        self.assertIn(network.name, summary)
        self.assertIn("deleted", summary)


class NetworkUpdateExecuteTest(FlowTestCase):
    def test_execute_sends_rest_put_shape(self) -> None:
        network = Network.objects.create(name="net1", net_string="10.0.0.0/24")
        action = self._action(
            self._flow(),
            action_type="network.update",
            target_uuid=network.uuid,
            values={"net_string": "192.168.0.0/16"},
            base_values={"net_string": "10.0.0.0/24"},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types.networks.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            # async_to_sync mirrors the production executor: the
            # thread-sensitive ORM work runs on the caller thread
            summary = async_to_sync(NetworkUpdate().execute)(action, request=make_request())

        self.assertIn("updated", summary)
        execute_call = proxy_cls.return_value.execute.call_args
        self.assertIs(execute_call[0][0].handler, Networks)
        # The PUT is form-shaped: every saved field travels, proposed win
        self.assertEqual(
            execute_call[0][2],
            {"name": "net1", "net_string": "192.168.0.0/16", "tags": []},
        )
