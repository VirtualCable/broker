"""
Tests for uds.core.types.notifiers
"""

import unittest

from uds.core.types.notifiers import NotificationGroup, NotificationGroupInfo


class NotificationGroupTest(unittest.TestCase):
    """
    Test NotificationGroup enum and its metadata
    """

    def test_members_metadata(self) -> None:
        self.assertEqual(NotificationGroup.LOG.kind, "log")
        self.assertFalse(NotificationGroup.LOG.repeatable)
        self.assertEqual(NotificationGroup.EVENT.kind, "event")
        self.assertTrue(NotificationGroup.EVENT.repeatable)

    def test_value_is_group_info(self) -> None:
        self.assertIsInstance(NotificationGroup.LOG.value, NotificationGroupInfo)
        self.assertEqual(NotificationGroup.LOG.value.kind, "log")
        self.assertIsInstance(NotificationGroup.EVENT.value, NotificationGroupInfo)
        self.assertEqual(NotificationGroup.EVENT.value.kind, "event")

    def test_str_repr(self) -> None:
        self.assertEqual(str(NotificationGroup.LOG), "log")
        self.assertEqual(str(NotificationGroup.EVENT), "event")
        self.assertEqual(repr(NotificationGroup.LOG), "log")

    def test_info_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            NotificationGroup.LOG.value.kind = "other"  # type: ignore[misc]

    def test_from_kind_known(self) -> None:
        self.assertIs(NotificationGroup.from_kind("log"), NotificationGroup.LOG)
        self.assertIs(NotificationGroup.from_kind("event"), NotificationGroup.EVENT)

    def test_from_kind_unknown_raises(self) -> None:
        with self.assertRaises(ValueError):
            NotificationGroup.from_kind("unknown")

    def test_from_kind_unknown_with_default(self) -> None:
        self.assertIs(
            NotificationGroup.from_kind("unknown", default=NotificationGroup.LOG),
            NotificationGroup.LOG,
        )


class NotifierAcceptsTest(unittest.TestCase):
    """
    Test that base Notifier declares LOG as default accepted group
    """

    def test_default_accepts(self) -> None:
        from uds.core import messaging

        self.assertEqual(messaging.Notifier.accepts, frozenset((NotificationGroup.LOG,)))


if __name__ == "__main__":
    unittest.main()
