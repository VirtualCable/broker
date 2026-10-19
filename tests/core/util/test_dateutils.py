#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
"""
Author: Andres Schumann, aschumann at virtualcable dot es

Unit tests for ``uds.core.util.dateutils.as_local_str``.
"""

import datetime

from django.test import override_settings

from uds.core.util import dateutils

from ...utils.test import UDSTestCase

UTC_NOON = datetime.datetime(2024, 1, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
UTC_SUMMER_NOON = datetime.datetime(2024, 7, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)

@override_settings(TIME_ZONE="Europe/Madrid")
class AsLocalStrTest(UDSTestCase):
    def test_utc_converted_to_winter_time(self) -> None:
        self.assertEqual(dateutils.as_local_str(UTC_NOON), "2024-01-15 13:00:00")

    def test_utc_converted_to_summer_time(self) -> None:
        self.assertEqual(dateutils.as_local_str(UTC_SUMMER_NOON), "2024-07-15 14:00:00")

    def test_conversion_can_cross_midnight(self) -> None:
        late = datetime.datetime(2024, 1, 14, 23, 30, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(dateutils.as_local_str(late), "2024-01-15 00:30:00")

    def test_naive_is_taken_as_local_time(self) -> None:
        self.assertEqual(dateutils.as_local_str(datetime.datetime(2024, 1, 15, 12, 0, 0)), "2024-01-15 12:00:00")

    def test_none_is_empty(self) -> None:
        self.assertEqual(dateutils.as_local_str(None), "")
