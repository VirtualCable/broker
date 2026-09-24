"""Functional tests for auditable event notifications (EventType) on REST operations.

The NotificationsManager persistence (local sqlite "persistent" db) is not
available on the test environment, so NotificationsManager.notify is mocked and
the call arguments (group, identificator, level, message) are asserted instead.
"""

import typing
from unittest import mock

from uds.core import types
from uds.core.audit.immutable import ImmutableLogger
from uds.core.managers import notifications as notifications_module
from uds.core.types import notifiers

from ...fixtures import rest as rest_fixtures
from ...utils import rest
from ...utils.test import UDSClient


def events_of(mocked: mock.MagicMock, event_type: notifiers.EventType) -> list[typing.Any]:
    """Returns the notify() calls for a given EventType (identificator arg)."""
    return [call for call in mocked.call_args_list if call.args[1] == event_type.value]


def message_of(call: typing.Any) -> str:
    """Message argument of a notify() call."""
    return typing.cast(str, call.args[3])


class AuditEventsTest(rest.test.RESTTestCase):
    """Admin CRUD operations and user login/logout emit EventType notifications."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def test_detail_user_create_emits_rest_create(self) -> None:
        url = f"authenticators/{self.auth.uuid}/users"
        user_dct = rest_fixtures.createUser(groups=[self.simple_groups[0].uuid])

        with (
            mock.patch.object(notifications_module.NotificationsManager, "notify") as notify,
            mock.patch.object(ImmutableLogger, "is_enabled", return_value=True),
            mock.patch.object(ImmutableLogger, "append_object") as append,
        ):
            response = self.client.rest_put(url, user_dct)

        self.assertEqual(response.status_code, 200)
        calls = events_of(notify, notifiers.EventType.REST_CREATE)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0], notifiers.NotificationGroup.EVENT)
        self.assertEqual(calls[0].args[2], types.log.LogLevel.OTHER)
        self.assertIn(self.admins[0].pretty_name, message_of(calls[0]))
        self.assertIn(self.auth.name, message_of(calls[0]))
        self.assertEqual(events_of(notify, notifiers.EventType.REST_OPERATION), [])
        # The unified chokepoint writes the immutable audit entry too
        self.assertEqual(append.call_count, 1)
        self.assertEqual(append.call_args.args[0]["t"], "rest")
        self.assertEqual(append.call_args.args[0]["m"], "PUT")

    def test_detail_user_modify_emits_rest_update(self) -> None:
        url = f"authenticators/{self.auth.uuid}/users"
        target = self.plain_users[0]
        user_dct = rest_fixtures.createUser(
            id=target.uuid,
            name=target.name,
            real_name="modified by audit test",
            groups=[self.simple_groups[0].uuid],
        )

        with mock.patch.object(notifications_module.NotificationsManager, "notify") as notify:
            response = self.client.rest_put(url + f"/{target.uuid}", user_dct)

        self.assertEqual(response.status_code, 200, response.content)
        calls = events_of(notify, notifiers.EventType.REST_UPDATE)
        self.assertEqual(len(calls), 1)
        # The user still exists when logged, so the path is resolved to names
        self.assertIn(target.name, message_of(calls[0]))
        self.assertIn(self.auth.name, message_of(calls[0]))

    def test_detail_user_delete_emits_rest_delete(self) -> None:
        url = f"authenticators/{self.auth.uuid}/users"
        target = self.plain_users[0]

        with mock.patch.object(notifications_module.NotificationsManager, "notify") as notify:
            response = self.client.rest_delete(url + f"/{target.uuid}")

        self.assertEqual(response.status_code, 200)
        calls = events_of(notify, notifiers.EventType.REST_DELETE)
        self.assertEqual(len(calls), 1)
        # The user is gone when logged, so the raw uuid remains on the path
        self.assertIn(target.uuid, message_of(calls[0]))
        self.assertIn(self.auth.name, message_of(calls[0]))

    def test_custom_method_post_emits_rest_operation(self) -> None:
        url = f"authenticators/{self.auth.uuid}/users/{self.plain_users[0].uuid}/token"

        with mock.patch.object(notifications_module.NotificationsManager, "notify") as notify:
            response = self.client.rest_post(url)

        self.assertEqual(response.status_code, 200, response.content)
        calls = events_of(notify, notifiers.EventType.REST_OPERATION)
        self.assertEqual(len(calls), 1)
        self.assertIn("/token", message_of(calls[0]))
        self.assertEqual(events_of(notify, notifiers.EventType.REST_CREATE), [])

    def test_denied_model_request_writes_immutable_entry(self) -> None:
        """A 403 on a model handler (e.g. path scope denial with a valid
        session) is kept on the immutable audit as evidence, without
        emitting events."""
        from types import SimpleNamespace

        from uds.REST import log as rest_log
        from uds.REST.handlers import Handler

        handler = typing.cast(
            "Handler",
            SimpleNamespace(
                request=SimpleNamespace(
                    method="DELETE", path="/uds/rest/authenticators/xxxx", ip="1.2.3.4", user=None
                ),
                MODEL=int,
                DETAIL={},
                _args=["xxxx"],
            ),
        )

        with (
            mock.patch.object(notifications_module.NotificationsManager, "notify") as notify,
            mock.patch.object(ImmutableLogger, "is_enabled", return_value=True),
            mock.patch.object(ImmutableLogger, "append_object") as append,
        ):
            rest_log.log_operation(handler, 403, types.log.LogLevel.ERROR)

        append.assert_called_once()
        entry = append.call_args.args[0]
        self.assertEqual(entry["t"], "rest")
        self.assertEqual(entry["c"], 403)
        self.assertTrue(entry["e"])
        self.assertEqual(entry["m"], "DELETE")
        self.assertEqual(entry["u"], "Unknown")
        self.assertEqual(events_of(notify, notifiers.EventType.REST_DELETE), [])

    def test_unauthenticated_request_leaves_denied_audit_entry(self) -> None:
        """A request with invalid credentials (no handler) is denied, and
        leaves syslog + immutable audit evidence without emitting events."""
        client = UDSClient()  # not logged in

        with (
            mock.patch.object(notifications_module.NotificationsManager, "notify") as notify,
            mock.patch.object(ImmutableLogger, "is_enabled", return_value=True),
            mock.patch.object(ImmutableLogger, "append_object") as append,
        ):
            response = client.get("/uds/rest/authenticators")

        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(events_of(notify, notifiers.EventType.REST_OPERATION), [])
        self.assertEqual(events_of(notify, notifiers.EventType.REST_CREATE), [])
        self.assertEqual(append.call_count, 1)
        entry = append.call_args.args[0]
        self.assertEqual(entry["t"], "rest")
        self.assertEqual(entry["c"], 403)
        self.assertTrue(entry["e"])
        self.assertEqual(entry["m"], "GET")
        self.assertEqual(entry["p"], "/uds/rest/authenticators")

    def test_master_authenticator_create_modify_delete_emit_events(self) -> None:
        payload: dict[str, typing.Any] = {
            "name": "audit-test-auth",
            "comments": "created by audit test",
            "tags": [],
            "priority": 1,
            "small_name": "audit-test",
            "state": "A",
            "net_filtering": "n",
            "data_type": "InternalDBAuth",
        }
        with mock.patch.object(notifications_module.NotificationsManager, "notify") as notify:
            # Create
            response = self.client.rest_post("authenticators", payload)
            self.assertEqual(response.status_code, 200, response.content)
            auth_uuid = response.json()["id"]

            calls = events_of(notify, notifiers.EventType.REST_CREATE)
            self.assertEqual(len(calls), 1)
            # Collection create: the new item is not on the path yet
            self.assertIn("POST /uds/rest/authenticators", message_of(calls[0]))

            # Modify
            payload["comments"] = "modified by audit test"
            response = self.client.rest_put(f"authenticators/{auth_uuid}", payload)
            self.assertEqual(response.status_code, 200, response.content)
            calls = events_of(notify, notifiers.EventType.REST_UPDATE)
            self.assertEqual(len(calls), 1)
            # Item exists when logged, so the name is resolved on the path
            self.assertIn("audit-test-auth", message_of(calls[0]))

            # Delete
            response = self.client.rest_delete(f"authenticators/{auth_uuid}")
            self.assertEqual(response.status_code, 200, response.content)
            calls = events_of(notify, notifiers.EventType.REST_DELETE)
            self.assertEqual(len(calls), 1)
            # Item is gone when logged, so the raw uuid remains
            self.assertIn(auth_uuid, message_of(calls[0]))

    def test_login_emits_login_event(self) -> None:
        client = UDSClient()
        with (
            mock.patch.object(notifications_module.NotificationsManager, "notify") as notify,
            mock.patch.object(ImmutableLogger, "is_enabled", return_value=True),
            mock.patch.object(ImmutableLogger, "append_object") as append,
        ):
            response = client.post(
                "/uds/rest/auth/login",
                data={
                    "auth_id": self.auth.uuid,
                    "username": self.admins[0].name,
                    "password": self.admins[0].name,
                },
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200, response.content)

        calls = events_of(notify, notifiers.EventType.LOGIN)
        self.assertEqual(len(calls), 1)
        self.assertIn(self.admins[0].name, message_of(calls[0]))
        self.assertIn(self.auth.name, message_of(calls[0]))
        # Non model handlers (auth) go the semantic path: no rest.* event and
        # no "rest" audit entry (log_login writes the "login" one instead)
        self.assertEqual(events_of(notify, notifiers.EventType.REST_OPERATION), [])
        self.assertEqual(
            [call for call in append.call_args_list if call.args[0].get("t") == "rest"],
            [],
        )
        self.assertEqual(append.call_args.args[0]["t"], "login")

    def test_failed_login_emits_login_failed_event(self) -> None:
        client = UDSClient()
        with mock.patch.object(notifications_module.NotificationsManager, "notify") as notify:
            response = client.post(
                "/uds/rest/auth/login",
                data={
                    "auth_id": self.auth.uuid,
                    "username": self.plain_users[0].name,
                    "password": "wrong-password",
                },
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["result"], "error")

        calls = events_of(notify, notifiers.EventType.LOGIN_FAILED)
        self.assertEqual(len(calls), 1)
        self.assertIn(self.plain_users[0].name, message_of(calls[0]))
        self.assertIn("Invalid password", message_of(calls[0]))
        # No success event must be emitted on failed login
        self.assertEqual(events_of(notify, notifiers.EventType.LOGIN), [])

    def test_logout_emits_logout_event(self) -> None:
        with mock.patch.object(notifications_module.NotificationsManager, "notify") as notify:
            rest.logout(self, self.client)

        calls = events_of(notify, notifiers.EventType.LOGOUT)
        self.assertEqual(len(calls), 1)
        self.assertIn(self.admins[0].name, message_of(calls[0]))
