"""``authenticator.update``: registration, discovery, CAS and execution shape."""

from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_types, get as registry_get
from uds.mutability.types.authenticators import AuthenticatorUpdate
from uds.REST.methods.authenticators import Authenticators

from tests.fixtures.authenticators import create_db_authenticator
from tests.mcp.mutability._helpers import FlowTestCase, make_request


class AuthenticatorUpdateRegistryTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("authenticator.update")
        self.assertIs(found, AuthenticatorUpdate)
        self.assertIn("authenticator.update", [t.type_id for t in all_types()])

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
        # InternalDB module fields: unique_by_host/reverse_dns are readonly
        # in the gui (never mutable); accepts_proxy stays proposable
        self.assertNotIn("unique_by_host", by_name)
        self.assertNotIn("reverse_dns", by_name)
        self.assertIn("accepts_proxy", by_name)
        self.assertTrue(by_name["accepts_proxy"]["from_instance"])
        # m2m relations and FK references are out of the surface
        self.assertNotIn("networks", by_name)
        self.assertNotIn("mfa_id", by_name)

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
