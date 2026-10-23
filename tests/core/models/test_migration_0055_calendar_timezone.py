import datetime
import zoneinfo

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings

MADRID = zoneinfo.ZoneInfo("Europe/Madrid")
UTC = datetime.timezone.utc

BEFORE = [("uds", "0054_actionflow_flowaction")]
AFTER = [("uds", "0055_calendar_timezone")]


@override_settings(TIME_ZONE="Europe/Madrid")
class Migration0055Test(TransactionTestCase):
    """Rules written by 4.0 as server-local wall clocks become real UTC instants."""

    def _migrate(self, targets: list[tuple[str, str]]):  # type: ignore[no-untyped-def]
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def tearDown(self) -> None:
        self._migrate(AFTER)
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def _seed_like_4_0(self, apps) -> None:  # type: ignore[no-untyped-def]
        Calendar = apps.get_model("uds", "Calendar")
        CalendarRule = apps.get_model("uds", "CalendarRule")
        calendar = Calendar.objects.create(name="office", comments="", uuid="cal-office")
        # 4.0 stored the digits the admin typed (08:00 local) with no zone;
        # 5.0 reads that column back as UTC.
        CalendarRule.objects.create(
            calendar=calendar,
            uuid="rule-winter",
            name="winter",
            comments="",
            start=datetime.datetime(2026, 1, 5, 8, 0, tzinfo=UTC),
            end=datetime.date(2026, 1, 31),
            frequency="DAILY",
            interval=1,
            duration=1,
            duration_unit="HOURS",
        )
        CalendarRule.objects.create(
            calendar=calendar,
            uuid="rule-summer",
            name="summer",
            comments="",
            start=datetime.datetime(2026, 7, 6, 8, 0, tzinfo=UTC),
            end=None,
            frequency="DAILY",
            interval=1,
            duration=1,
            duration_unit="HOURS",
        )

    def test_rules_become_utc_and_calendars_get_the_server_zone(self) -> None:
        self._seed_like_4_0(self._migrate(BEFORE))
        apps = self._migrate(AFTER)
        Calendar = apps.get_model("uds", "Calendar")
        CalendarRule = apps.get_model("uds", "CalendarRule")

        self.assertEqual(Calendar.objects.get(uuid="cal-office").tz, "Europe/Madrid")

        winter = CalendarRule.objects.get(uuid="rule-winter")
        self.assertEqual(winter.start, datetime.datetime(2026, 1, 5, 7, 0, tzinfo=UTC))  # 08:00 CET
        self.assertEqual(winter.end, datetime.datetime(2026, 1, 31, 22, 59, 59, 999999, tzinfo=UTC))  # end of 31-01 CET

        summer = CalendarRule.objects.get(uuid="rule-summer")
        self.assertEqual(summer.start, datetime.datetime(2026, 7, 6, 6, 0, tzinfo=UTC))  # 08:00 CEST
        self.assertIsNone(summer.end)

    def test_logs_the_zone_and_the_number_of_rules_converted(self) -> None:
        self._seed_like_4_0(self._migrate(BEFORE))
        with self.assertLogs("uds.migrations", level="INFO") as logs:
            self._migrate(AFTER)
        self.assertTrue(
            any("2 calendar rules" in line and "Europe/Madrid" in line for line in logs.output), logs.output
        )

    def test_reverse_restores_the_4_0_values(self) -> None:
        self._seed_like_4_0(self._migrate(BEFORE))
        self._migrate(AFTER)
        apps = self._migrate(BEFORE)
        CalendarRule = apps.get_model("uds", "CalendarRule")

        winter = CalendarRule.objects.get(uuid="rule-winter")
        self.assertEqual(winter.start, datetime.datetime(2026, 1, 5, 8, 0, tzinfo=UTC))
        self.assertEqual(winter.end, datetime.date(2026, 1, 31))
        summer = CalendarRule.objects.get(uuid="rule-summer")
        self.assertEqual(summer.start, datetime.datetime(2026, 7, 6, 8, 0, tzinfo=UTC))
        self.assertIsNone(summer.end)
