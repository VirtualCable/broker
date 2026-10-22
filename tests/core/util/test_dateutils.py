#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
"""
Author: Andres Schumann, aschumann at virtualcable dot es

Unit tests for ``uds.core.util.dateutils.as_utc_iso``.
"""

import datetime

from django.test import override_settings
from django.utils import timezone

from uds.core.util import dateutils

from ...utils.test import UDSTestCase

UTC_NOON = datetime.datetime(2024, 1, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
UTC_SUMMER_NOON = datetime.datetime(2024, 7, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)

@override_settings(TIME_ZONE="Europe/Madrid")
class AsUtcIsoTest(UDSTestCase):
    def test_utc_kept_as_utc(self) -> None:
        self.assertEqual(dateutils.as_utc_iso(UTC_NOON), "2024-01-15T12:00:00Z")

    def test_does_not_depend_on_broker_timezone(self) -> None:
        with override_settings(TIME_ZONE="America/New_York"):
            self.assertEqual(dateutils.as_utc_iso(UTC_SUMMER_NOON), "2024-07-15T12:00:00Z")
        self.assertEqual(dateutils.as_utc_iso(UTC_SUMMER_NOON), "2024-07-15T12:00:00Z")

    def test_offset_datetime_converted_to_utc(self) -> None:
        madrid_summer = datetime.datetime(2024, 7, 15, 14, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
        self.assertEqual(dateutils.as_utc_iso(madrid_summer), "2024-07-15T12:00:00Z")

    def test_conversion_can_cross_midnight(self) -> None:
        madrid_early = datetime.datetime(2024, 1, 15, 0, 30, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=1)))
        self.assertEqual(dateutils.as_utc_iso(madrid_early), "2024-01-14T23:30:00Z")

    def test_naive_is_taken_as_broker_local_time(self) -> None:
        timezone.deactivate()  # other tests leave UTC activated; the broker never activates one
        self.assertEqual(dateutils.as_utc_iso(datetime.datetime(2024, 1, 15, 13, 0, 0)), "2024-01-15T12:00:00Z")  # noqa: DTZ001

    def test_none_is_empty(self) -> None:
        self.assertEqual(dateutils.as_utc_iso(None), "")
