"""Functional tests for user service session events (USER_SERVICE_LOGIN/LOGOUT).

The NotificationsManager persistence (local sqlite "persistent" db) is not
available on the test environment, so NotificationsManager.notify is mocked and
the call arguments are asserted instead.
"""

import typing
from unittest import mock

from uds.core.audit.immutable import ImmutableLogger
from uds.core.managers import notifications as notifications_module
from uds.core.osmanagers.osmanager import OSManager
from uds.core.types import notifiers
from uds.core.util import config

from ..fixtures import services as services_fixtures
from ..utils import rest


def events_of(mocked: mock.MagicMock, event_type: notifiers.EventType) -> list[typing.Any]:
    """Returns the notify() calls for a given EventType (identificator arg)."""
    return [call for call in mocked.call_args_list if call.args[1] == event_type.value]


def message_of(call: typing.Any) -> str:
    """Message argument of a notify() call."""
    return typing.cast(str, call.args[3])


class UserServiceEventsTest(rest.test.RESTTestCase):
    """User service login/logout through the OS manager emit EventType notifications."""

    def test_logged_in_emits_user_service_login(self) -> None:
        userservice = self.user_service_managed

        with mock.patch.object(notifications_module.NotificationsManager, "notify") as notify:
            OSManager.logged_in(userservice, "remote-user")

        calls = events_of(notify, notifiers.EventType.USER_SERVICE_LOGIN)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0], notifiers.NotificationGroup.EVENT)
        self.assertIn("remote-user", message_of(calls[0]))
        self.assertIn(userservice.friendly_name, message_of(calls[0]))

    def test_logged_in_writes_immutable_audit_entry(self) -> None:
        userservice = self.user_service_managed

        with (
            mock.patch.object(ImmutableLogger, "is_enabled", return_value=True),
            mock.patch.object(ImmutableLogger, "append_object") as append,
        ):
            OSManager.logged_in(userservice, "remote-user")

        self.assertEqual(append.call_count, 1)
        entry = append.call_args.args[0]
        self.assertEqual(entry["t"], "userservice")
        self.assertEqual(entry["r"], "login")
        self.assertEqual(entry["u"], "remote-user")
        self.assertEqual(entry["s"], userservice.friendly_name)
        self.assertEqual(entry["p"], userservice.deployed_service.name)

    def test_logged_out_emits_user_service_logout(self) -> None:
        userservice = self.user_service_managed
        OSManager.logged_in(userservice, "remote-user")

        with mock.patch.object(notifications_module.NotificationsManager, "notify") as notify:
            OSManager.logged_out(userservice, "remote-user")

        calls = events_of(notify, notifiers.EventType.USER_SERVICE_LOGOUT)
        self.assertEqual(len(calls), 1)
        self.assertIn("remote-user", message_of(calls[0]))
        self.assertIn(userservice.friendly_name, message_of(calls[0]))

    def test_logged_out_writes_immutable_audit_entry(self) -> None:
        userservice = self.user_service_managed
        OSManager.logged_in(userservice, "remote-user")

        with (
            mock.patch.object(ImmutableLogger, "is_enabled", return_value=True),
            mock.patch.object(ImmutableLogger, "append_object") as append,
        ):
            OSManager.logged_out(userservice, "remote-user")

        self.assertEqual(append.call_count, 1)
        entry = append.call_args.args[0]
        self.assertEqual(entry["t"], "userservice")
        self.assertEqual(entry["r"], "logout")
        self.assertEqual(entry["u"], "remote-user")

    def test_exclusive_logout_does_not_emit(self) -> None:
        """With exclusive logout enabled, a logout with pending sessions
        (logins counter > 0 after decrement) does not emit."""
        userservice = services_fixtures.create_db_one_assigned_userservice(
            self.provider,
            self.admins[0],
            self.groups,
            "managed",
        )
        OSManager.logged_in(userservice, "remote-user")
        OSManager.logged_in(userservice, "remote-user")
        OSManager.logged_in(userservice, "remote-user")

        with (
            mock.patch.object(notifications_module.NotificationsManager, "notify") as notify,
            mock.patch.object(config.GlobalConfig.EXCLUSIVE_LOGOUT, "as_bool", return_value=True),
        ):
            OSManager.logged_out(userservice, "remote-user")

        self.assertEqual(events_of(notify, notifiers.EventType.USER_SERVICE_LOGOUT), [])
