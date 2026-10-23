import datetime
import zoneinfo

from django.utils import timezone

from uds.core import types
from uds.core.util import calendar
from uds.models import Calendar

from ...fixtures import services as fixtures_services
from ...utils.test import UDSTestCase

UTC = datetime.timezone.utc
MADRID = zoneinfo.ZoneInfo("Europe/Madrid")


class CalendarUtcTest(UDSTestCase):
    """Rule start/end are UTC instants and rules repeat in UTC.

    The calendar time zone is only for showing and editing the rules; the
    result of the checker must not depend on the time zone Django is running in.
    """

    def setUp(self) -> None:
        calendar.CalendarChecker.cache.purge()
        self.cal = Calendar.objects.create(name="office", tz="Europe/Madrid")
        self.cal.rules.create(
            name="08-09 Madrid, created in winter",
            comments="",
            start=datetime.datetime(2026, 1, 5, 7, 0, tzinfo=UTC),
            frequency="DAILY",
            interval=1,
            duration=1,
            duration_unit="HOURS",
        )
        self.cal = Calendar.objects.get(pk=self.cal.pk)

    def tearDown(self) -> None:
        timezone.deactivate()

    def _active_utc(self, day: datetime.date) -> list[str]:
        base = datetime.datetime.combine(day, datetime.time(0, 0), UTC)
        return [
            (base + datetime.timedelta(minutes=m)).strftime("%H:%M")
            for m in range(0, 24 * 60, 30)
            if calendar.CalendarChecker(self.cal).check(base + datetime.timedelta(minutes=m))
        ]

    def test_rule_is_active_at_its_utc_time_in_any_server_zone(self) -> None:
        for tz in (UTC, MADRID, zoneinfo.ZoneInfo("Asia/Tokyo")):
            with self.subTest(tz=str(tz)):
                timezone.activate(tz)
                calendar.CalendarChecker.cache.purge()
                for day in (datetime.date(2026, 1, 20), datetime.date(2026, 7, 20)):
                    self.assertEqual(self._active_utc(day), ["07:00", "07:30"])

    def test_check_and_next_event_agree(self) -> None:
        timezone.activate(MADRID)
        for day in (datetime.date(2026, 1, 20), datetime.date(2026, 7, 20)):
            with self.subTest(day=day.isoformat()):
                starts_at = calendar.CalendarChecker(self.cal).next_event(
                    datetime.datetime.combine(day, datetime.time(0, 0), UTC)
                )
                assert starts_at is not None
                self.assertEqual(starts_at.astimezone(UTC).time(), datetime.time(7, 0))
                self.assertTrue(
                    calendar.CalendarChecker(self.cal).check(starts_at + datetime.timedelta(minutes=30))
                )

    def test_pool_access_follows_the_utc_window(self) -> None:
        timezone.activate(MADRID)
        pool = fixtures_services.create_db_servicepool(
            fixtures_services.create_db_service(fixtures_services.create_db_provider())
        )
        pool.fallbackAccess = types.states.State.DENY
        pool.save()
        pool.calendarAccess.create(calendar=self.cal, access=types.states.State.ALLOW, priority=0)

        at = lambda h, m: datetime.datetime(2026, 1, 20, h, m, tzinfo=UTC)
        self.assertTrue(pool.is_access_allowed(at(7, 30)))
        self.assertFalse(pool.is_access_allowed(at(6, 30)))
        self.assertFalse(pool.is_access_allowed(at(8, 30)))

    def test_end_is_an_instant(self) -> None:
        timezone.activate(MADRID)
        rule = self.cal.rules.get()
        rule.end = datetime.datetime(2026, 1, 21, 22, 59, 59, tzinfo=UTC)  # 21-01 23:59:59 Madrid
        rule.save()
        self.cal = Calendar.objects.get(pk=self.cal.pk)
        calendar.CalendarChecker.cache.purge()

        self.assertEqual(self._active_utc(datetime.date(2026, 1, 21)), ["07:00", "07:30"])
        self.assertEqual(self._active_utc(datetime.date(2026, 1, 22)), [])
