"""``mfa.update``: registration, discovery, CAS and execution shape."""

from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_types, get as registry_get
from uds.mutability.types.mfa import MFAUpdate
from uds.REST.methods.mfas import MFA

from tests.fixtures.mfas import create_db_mfa
from tests.mcp.mutability._helpers import FlowTestCase, make_request


class MFAUpdateRegistryTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("mfa.update")
        self.assertIs(found, MFAUpdate)
        self.assertIn("mfa.update", [t.type_id for t in all_types()])

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            MFAUpdate().resolve_target("00000000-0000-0000-0000-000000000000")


class MFAUpdateFieldsTest(FlowTestCase):
    def test_field_definitions_cover_model_columns(self) -> None:
        create_db_mfa()  # registers TestMFA in the factory
        defs = MFAUpdate().field_definitions("testMFA")
        by_name = {d["name"]: d for d in defs}

        for name in ("name", "comments", "tags", "remember_device", "validity"):
            self.assertIn(name, by_name)
        # remember_device and validity are model columns, not instance fields
        self.assertFalse(by_name["remember_device"]["from_instance"])
        self.assertFalse(by_name["validity"]["from_instance"])

    def test_snapshot_and_fingerprint_track_model(self) -> None:
        mfa = create_db_mfa(remember_device=2, validity=60)
        action_type = MFAUpdate()

        fields = action_type.etag_fields(action_type.for_type_of(mfa))
        snapshot = action_type.snapshot_values(mfa, fields)
        self.assertEqual(snapshot["name"], mfa.name)
        self.assertEqual(snapshot["remember_device"], 2)
        self.assertEqual(snapshot["validity"], 60)

        base = action_type.fingerprint(mfa)
        self.assertEqual(base, action_type.fingerprint(mfa))

        mfa.validity = 120
        mfa.save(update_fields=["validity"])
        self.assertNotEqual(base, action_type.fingerprint(mfa))


class MFAUpdateExecuteTest(FlowTestCase):
    def test_execute_merges_over_the_cas_verified_values(self) -> None:
        mfa = create_db_mfa(remember_device=2, validity=60)
        action = self._action(
            self._flow(),
            action_type="mfa.update",
            target_uuid=mfa.uuid,
            values={"validity": 300},
            base_values={"validity": 60},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types._module_update.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            summary = async_to_sync(MFAUpdate().execute)(action, request=make_request())

        self.assertIn("updated", summary)
        execute_call = proxy_cls.return_value.execute.call_args
        self.assertIs(execute_call[0][0].handler, MFA)
        params = execute_call[0][2]
        for name in ("name", "comments", "tags", "remember_device", "validity"):
            self.assertIn(name, params)
        self.assertEqual(params["validity"], 300)
        self.assertEqual(params["remember_device"], 2)
        self.assertEqual(params["data_type"], "testMFA")
