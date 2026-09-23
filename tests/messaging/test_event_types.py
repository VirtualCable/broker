"""Tests for uds.core.types.notifiers.EventType."""

import unittest
from unittest import mock

from uds.core import types
from uds.core.managers import notifications as notifications_module
from uds.core.types import notifiers


class EventTypeTest(unittest.TestCase):
    """EventType values and the notify() helper behavior."""

    def test_values_are_domain_prefixed(self) -> None:
        self.assertEqual(notifiers.EventType.REST_CREATE.value, "rest.create")
        self.assertEqual(notifiers.EventType.REST_UPDATE.value, "rest.update")
        self.assertEqual(notifiers.EventType.REST_DELETE.value, "rest.delete")
        self.assertEqual(notifiers.EventType.REST_OPERATION.value, "rest.operation")
        self.assertEqual(notifiers.EventType.LOGIN.value, "user.login")
        self.assertEqual(notifiers.EventType.LOGIN_FAILED.value, "user.login_failed")
        self.assertEqual(notifiers.EventType.LOGOUT.value, "user.logout")
        self.assertEqual(notifiers.EventType.USER_SERVICE_LOGIN.value, "userservice.login")
        self.assertEqual(notifiers.EventType.USER_SERVICE_LOGOUT.value, "userservice.logout")

    def test_notify_defaults_to_other_level(self) -> None:
        manager = mock.MagicMock()
        with mock.patch.object(notifications_module.NotificationsManager, "manager", return_value=manager):
            notifiers.EventType.REST_CREATE.notify("something happened")

        manager.notify.assert_called_once_with(
            notifiers.NotificationGroup.EVENT,
            "rest.create",
            types.log.LogLevel.OTHER,
            "something happened",
        )

    def test_notify_with_explicit_level(self) -> None:
        manager = mock.MagicMock()
        with mock.patch.object(notifications_module.NotificationsManager, "manager", return_value=manager):
            notifiers.EventType.LOGIN.notify("user login", types.log.LogLevel.WARNING)

        manager.notify.assert_called_once_with(
            notifiers.NotificationGroup.EVENT,
            "user.login",
            types.log.LogLevel.WARNING,
            "user login",
        )

    def test_notify_uses_str_value_of_member(self) -> None:
        # StrEnum: the value itself is the identificator stored on the notification
        self.assertEqual(str(notifiers.EventType.REST_DELETE), "rest.delete")
        self.assertEqual(notifiers.EventType.REST_DELETE.value, "rest.delete")
