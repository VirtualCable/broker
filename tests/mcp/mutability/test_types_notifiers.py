"""``notifier.update``: registration, discovery, CAS and execution shape."""

from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_types, get as registry_get
from uds.mutability.types.notifiers import NotifierUpdate
from uds.REST.methods.notifiers import Notifiers

from tests.fixtures.notifiers import createEmailNotifier
from tests.mcp.mutability._helpers import FlowTestCase, make_request


class NotifierUpdateRegistryTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("notifier.update")
        self.assertIs(found, NotifierUpdate)
        self.assertIn("notifier.update", [t.type_id for t in all_types()])

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            NotifierUpdate().resolve_target("00000000-0000-0000-0000-000000000000")


class NotifierUpdateFieldsTest(FlowTestCase):
    def test_field_definitions_cover_model_columns_and_module_fields(self) -> None:
        notifier = createEmailNotifier()
        defs = NotifierUpdate().field_definitions(notifier.data_type)
        by_name = {d["name"]: d for d in defs}

        for name in ("name", "comments", "tags", "level", "enabled"):
            self.assertIn(name, by_name)
        # Email module configuration fields travel as instance fields
        self.assertIn("hostname", by_name)
        self.assertTrue(by_name["hostname"]["from_instance"])
        self.assertFalse(by_name["enabled"]["from_instance"])
        # Passwords are flagged as secrets
        self.assertTrue(by_name["password"]["secret"])

    def test_snapshot_and_fingerprint_track_model_and_instance(self) -> None:
        notifier = createEmailNotifier()
        action_type = NotifierUpdate()

        fields = action_type.etag_fields(action_type.for_type_of(notifier))
        snapshot = action_type.snapshot_values(notifier, fields)
        self.assertEqual(snapshot["name"], notifier.name)
        self.assertIn("level", snapshot)
        self.assertIn("hostname", snapshot)

        base = action_type.fingerprint(notifier)
        self.assertEqual(base, action_type.fingerprint(notifier))

        notifier.comments = "changed"
        notifier.save(update_fields=["comments"])
        self.assertNotEqual(base, action_type.fingerprint(notifier))


class NotifierUpdateExecuteTest(FlowTestCase):
    def test_execute_merges_over_the_cas_verified_values(self) -> None:
        notifier = createEmailNotifier()
        action = self._action(
            self._flow(),
            action_type="notifier.update",
            target_uuid=notifier.uuid,
            values={"level": "DEBUG"},
            base_values={"level": notifier.level},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types._module_update.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            summary = async_to_sync(NotifierUpdate().execute)(action, request=make_request())

        self.assertIn("updated", summary)
        execute_call = proxy_cls.return_value.execute.call_args
        self.assertIs(execute_call[0][0].handler, Notifiers)
        self.assertEqual(execute_call[0][0].path, "messaging/notifiers/{uuid}")
        params = execute_call[0][2]
        for name in ("name", "comments", "tags", "level", "enabled"):
            self.assertIn(name, params)
        self.assertEqual(params["level"], "DEBUG")
        self.assertEqual(params["data_type"], "emailNotifications")
