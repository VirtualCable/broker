"""Functional tests for auditable event notifications (EventType) on REST operations.

The NotificationsManager persistence (local sqlite "persistent" db) is not
available on the test environment, so NotificationsManager.notify is mocked and
the call arguments (group, identificator, level, message) are asserted instead.
"""

import typing
from unittest import mock

from uds.core import types
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

    def test_detail_user_create_emits_admin_create(self) -> None:
        url = f"authenticators/{self.auth.uuid}/users"
        user_dct = rest_fixtures.createUser(groups=[self.simple_groups[0].uuid])

        with mock.patch.object(notifications_module.NotificationsManager, "notify") as notify:
            response = self.client.rest_put(url, user_dct)

        self.assertEqual(response.status_code, 200)
        calls = events_of(notify, notifiers.EventType.ADMIN_CREATE)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0], notifiers.NotificationGroup.EVENT)
        self.assertEqual(calls[0].args[2], types.log.LogLevel.OTHER)
        self.assertIn(self.admins[0].pretty_name, message_of(calls[0]))
        self.assertIn(self.auth.name, message_of(calls[0]))

    def test_detail_user_modify_emits_admin_modify(self) -> None:
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
        calls = events_of(notify, notifiers.EventType.ADMIN_MODIFY)
        self.assertEqual(len(calls), 1)
        self.assertIn(target.uuid, message_of(calls[0]))
        self.assertIn(self.auth.name, message_of(calls[0]))

    def test_detail_user_delete_emits_admin_delete(self) -> None:
        url = f"authenticators/{self.auth.uuid}/users"
        target = self.plain_users[0]

        with mock.patch.object(notifications_module.NotificationsManager, "notify") as notify:
            response = self.client.rest_delete(url + f"/{target.uuid}")

        self.assertEqual(response.status_code, 200)
        calls = events_of(notify, notifiers.EventType.ADMIN_DELETE)
        self.assertEqual(len(calls), 1)
        self.assertIn(target.uuid, message_of(calls[0]))
        self.assertIn(self.auth.name, message_of(calls[0]))

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

            calls = events_of(notify, notifiers.EventType.ADMIN_CREATE)
            self.assertEqual(len(calls), 1)
            self.assertIn("audit-test-auth", message_of(calls[0]))

            # Modify
            payload["comments"] = "modified by audit test"
            response = self.client.rest_put(f"authenticators/{auth_uuid}", payload)
            self.assertEqual(response.status_code, 200, response.content)
            calls = events_of(notify, notifiers.EventType.ADMIN_MODIFY)
            self.assertEqual(len(calls), 1)
            self.assertIn("audit-test-auth", message_of(calls[0]))

            # Delete
            response = self.client.rest_delete(f"authenticators/{auth_uuid}")
            self.assertEqual(response.status_code, 200, response.content)
            calls = events_of(notify, notifiers.EventType.ADMIN_DELETE)
            self.assertEqual(len(calls), 1)
            self.assertIn("audit-test-auth", message_of(calls[0]))

    def test_login_emits_login_event(self) -> None:
        client = UDSClient()
        with mock.patch.object(notifications_module.NotificationsManager, "notify") as notify:
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

    def test_logout_emits_logout_event(self) -> None:
        with mock.patch.object(notifications_module.NotificationsManager, "notify") as notify:
            rest.logout(self, self.client)

        calls = events_of(notify, notifiers.EventType.LOGOUT)
        self.assertEqual(len(calls), 1)
        self.assertIn(self.admins[0].name, message_of(calls[0]))
