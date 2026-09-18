"""``authenticator.update`` / ``authenticator.create`` / ``.delete``: registration, discovery, CAS and execution shape."""

from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.consts.mcp import CREATE_TARGET_UUID
from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.base import ActionOperation, StalePolicy
from uds.mutability.types.authenticators import AuthenticatorUpdate
from uds.REST.methods.authenticators import Authenticators

from tests.fixtures.authenticators import create_db_authenticator
from tests.mcp.mutability._helpers import FlowTestCase, make_request, build_action


class AuthenticatorUpdateRegistryTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("authenticator.update")
        assert found is not None
        self.assertIs(type(found()), AuthenticatorUpdate)
        self.assertIn("authenticator.update", all_type_ids())

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            AuthenticatorUpdate().resolve_target("00000000-0000-0000-0000-000000000000")


class AuthenticatorUpdateFieldsTest(FlowTestCase):
    def test_field_definitions_cover_model_columns_and_module_fields(self) -> None:
        authenticator = create_db_authenticator()
        defs = AuthenticatorUpdate().field_definitions(authenticator.data_type)
        by_name = {d["name"]: d for d in defs}

        for name in ("name", "comments", "tags", "priority", "small_name", "state", "net_filtering"):
            self.assertIn(name, by_name)
        # The networks m2m relation and the mfa reference (uuid or empty)
        # are part of the surface
        self.assertIn("networks", by_name)
        self.assertIn("mfa_id", by_name)
        # InternalDB module fields: unique_by_host/reverse_dns are readonly
        # in the gui (never mutable); accepts_proxy stays proposable
        self.assertNotIn("unique_by_host", by_name)
        self.assertNotIn("reverse_dns", by_name)
        self.assertIn("accepts_proxy", by_name)
        self.assertTrue(by_name["accepts_proxy"]["from_instance"])

    def test_mfa_and_networks_snapshots_track_the_relations(self) -> None:
        from tests.fixtures.mfas import create_db_mfa
        from tests.fixtures.networks import create_network

        authenticator = create_db_authenticator()
        network = create_network()
        mfa = create_db_mfa()
        action_type = AuthenticatorUpdate()

        # Empty relations snapshot as empty values (the PUT shape)
        self.assertEqual(action_type.snapshot_values(authenticator, ["networks"])["networks"], [])
        self.assertEqual(action_type.snapshot_values(authenticator, ["mfa_id"])["mfa_id"], "")

        authenticator.networks.add(network)
        authenticator.mfa = mfa
        authenticator.save(update_fields=["mfa_id"])
        self.assertEqual(
            action_type.snapshot_values(authenticator, ["networks"])["networks"],
            [network.uuid],
        )
        self.assertEqual(action_type.snapshot_values(authenticator, ["mfa_id"])["mfa_id"], mfa.uuid)

    def test_snapshot_and_fingerprint_track_model_and_instance(self) -> None:
        authenticator = create_db_authenticator()
        action_type = AuthenticatorUpdate()

        fields = action_type.etag_fields(action_type.for_type_of(authenticator))
        snapshot = action_type.snapshot_values(authenticator, fields)
        self.assertEqual(snapshot["name"], authenticator.name)
        self.assertEqual(snapshot["state"], authenticator.state)
        self.assertNotIn("unique_by_host", snapshot)

        base = action_type.fingerprint(authenticator)
        self.assertEqual(base, action_type.fingerprint(authenticator))

        authenticator.priority = 5
        authenticator.save(update_fields=["priority"])
        self.assertNotEqual(base, action_type.fingerprint(authenticator))


class AuthenticatorUpdateExecuteTest(FlowTestCase):
    def test_execute_merges_over_the_cas_verified_values(self) -> None:
        authenticator = create_db_authenticator()
        action = self._action(
            self._flow(),
            action_type="authenticator.update",
            target_uuid=authenticator.uuid,
            values={"priority": 9},
            base_values={"priority": authenticator.priority},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types._module_update.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            summary = async_to_sync(AuthenticatorUpdate().execute)(action, request=make_request())

        self.assertIn("updated", summary)
        execute_call = proxy_cls.return_value.execute.call_args
        self.assertIs(execute_call[0][0].handler, Authenticators)
        params = execute_call[0][2]
        for name in ("name", "comments", "tags", "priority", "small_name", "state", "net_filtering"):
            self.assertIn(name, params)
        self.assertEqual(params["priority"], 9)
        self.assertEqual(params["data_type"], authenticator.data_type)
        # The relations ride the PUT too (uuid list / uuid or empty)
        self.assertEqual(params["networks"], [])


class AuthenticatorCreateDeleteTest(FlowTestCase):
    """authenticator.create / authenticator.delete: the module root verbs."""

    def test_supported_operations_derive_from_hooks(self) -> None:
        self.assertEqual(
            AuthenticatorUpdate.supported_operations(),
            frozenset({ActionOperation.UPDATE, ActionOperation.CREATE, ActionOperation.DELETE}),
        )

    def test_delete_rides_the_strict_deny_policy(self) -> None:
        delete = AuthenticatorUpdate(ActionOperation.DELETE)
        self.assertEqual(delete.full_id, "authenticator.delete")
        self.assertEqual(delete.get_stale_policy(), StalePolicy.DENY)

    def test_create_execution_posts_the_module_form_shape(self) -> None:
        authenticator = create_db_authenticator()
        create = AuthenticatorUpdate(ActionOperation.CREATE)
        action = build_action(
            action_type="authenticator.create",
            target_uuid=CREATE_TARGET_UUID,
            values={
                "data_type": authenticator.data_type,
                "name": "new auth",
                "priority": 5,
            },
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value={"id": "auth-uuid"})
            summary = async_to_sync(create.execute)(action, request=make_request())
            target, _request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual(
                (target.handler, target.method.value, target.args),
                (Authenticators, "POST", ()),
            )
            # Form shape: top-level columns (defaults included), the module
            # configuration under instance, subtype injected
            self.assertEqual(params["name"], "new auth")
            self.assertEqual(params["comments"], "")
            self.assertEqual(params["tags"], [])
            self.assertEqual(params["priority"], 5)
            self.assertEqual(params["data_type"], authenticator.data_type)
            self.assertIsInstance(params["instance"], dict)
        self.assertIn("new auth", summary)
        self.assertIn("auth-uuid", summary)

    def test_create_execution_carries_networks_in_params(self) -> None:
        authenticator = create_db_authenticator()
        create = AuthenticatorUpdate(ActionOperation.CREATE)
        action = build_action(
            action_type="authenticator.create",
            target_uuid=CREATE_TARGET_UUID,
            values={
                "data_type": authenticator.data_type,
                "name": "networked auth",
                "networks": ["net-uuid-1", "net-uuid-2"],
            },
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value={"id": "auth-uuid"})
            async_to_sync(create.execute)(action, request=make_request())
            _target, _request, params = proxy_cls.return_value.execute.call_args[0]
            # networks travels as a param (post_save reads it there),
            # never as module configuration
            self.assertEqual(params["networks"], ["net-uuid-1", "net-uuid-2"])
            self.assertNotIn("networks", params["instance"])

    def test_delete_execution_sends_canonical_delete(self) -> None:
        authenticator = create_db_authenticator()
        delete = AuthenticatorUpdate(ActionOperation.DELETE)
        action = build_action(
            action_type="authenticator.delete",
            target_uuid=authenticator.uuid,
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
                (Authenticators, "DELETE", (authenticator.uuid,)),
            )
            self.assertEqual(params, {})
        self.assertIn(authenticator.name, summary)
        self.assertIn("deleted", summary)
