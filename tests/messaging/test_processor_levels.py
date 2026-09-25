"""Tests for the level filter of the notification processor."""

import unittest

from uds.core.messaging.processor import reaches_provider
from uds.core.types.log import LogLevel
from uds.core.types.notifiers import NotificationGroup

ALL_GROUPS = frozenset(NotificationGroup)


class ReachesProviderTest(unittest.TestCase):
    def test_event_reaches_notifier_above_its_minimum_level(self) -> None:
        self.assertTrue(reaches_provider(LogLevel.ERROR, ALL_GROUPS, NotificationGroup.EVENT, LogLevel.OTHER))

    def test_log_below_minimum_level_is_filtered(self) -> None:
        self.assertFalse(reaches_provider(LogLevel.ERROR, ALL_GROUPS, NotificationGroup.LOG, LogLevel.INFO))

    def test_log_at_minimum_level_is_delivered(self) -> None:
        self.assertTrue(reaches_provider(LogLevel.ERROR, ALL_GROUPS, NotificationGroup.LOG, LogLevel.ERROR))

    def test_group_not_accepted_is_filtered_whatever_the_level(self) -> None:
        only_logs = frozenset((NotificationGroup.LOG,))
        self.assertFalse(reaches_provider(LogLevel.OTHER, only_logs, NotificationGroup.EVENT, LogLevel.OTHER))
