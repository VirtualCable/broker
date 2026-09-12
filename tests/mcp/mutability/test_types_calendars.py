"""``calendar.update`` and ``calendar_rule.update``: registration,
discovery, CAS (timestamps included) and execution shapes."""

import datetime
from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_types, get as registry_get
from uds.mutability.types.calendars import CalendarRuleUpdate, CalendarUpdate
from uds.models import Calendar, CalendarRule
from uds.REST.methods.calendarrules import CalendarRules

from tests.mcp.mutability._helpers import FlowTestCase, make_request

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


class CalendarUpdateTest(FlowTestCase):
    def test_calendar_update_is_registered(self) -> None:
        found = registry_get("calendar.update")
        self.assertIs(found, CalendarUpdate)
        self.assertIn("calendar.update", [t.type_id for t in all_types()])

    def test_fields_and_cas(self) -> None:
        calendar = Calendar.objects.create(name="work days")
        action_type = CalendarUpdate()

        names = [d["name"] for d in action_type.field_definitions("calendar")]
        self.assertEqual(sorted(names), ["comments", "name", "tags"])

        snapshot = action_type.snapshot_values(calendar, ["name", "tags"])
        self.assertEqual(snapshot, {"name": "work days", "tags": []})

        base = action_type.fingerprint(calendar)
        calendar.name = "work days changed"
        calendar.save(update_fields=["name"])
        self.assertNotEqual(base, action_type.fingerprint(calendar))

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            CalendarUpdate().resolve_target("00000000-0000-0000-0000-000000000000")


class CalendarRuleUpdateTest(FlowTestCase):
    def _rule(self) -> CalendarRule:
        calendar = Calendar.objects.create(name="holidays")
        return CalendarRule.objects.create(
            calendar=calendar,
            name="christmas",
            comments="",
            start=datetime.datetime(2026, 12, 24, 0, 0, tzinfo=datetime.timezone.utc),
            frequency="WEEKLY",
        )

    def test_calendar_rule_update_is_registered(self) -> None:
        found = registry_get("calendar_rule.update")
        self.assertIs(found, CalendarRuleUpdate)
        self.assertIn("calendar_rule.update", [t.type_id for t in all_types()])

    def test_field_definitions_are_the_save_item_shapes(self) -> None:
        names = [d["name"] for d in CalendarRuleUpdate().field_definitions("calendar_rule")]
        self.assertEqual(
            names,
            ["name", "comments", "frequency", "start", "end", "interval", "duration", "duration_unit"],
        )
        by_name = {d["name"]: d for d in CalendarRuleUpdate().field_definitions("calendar_rule")}
        self.assertEqual(
            by_name["frequency"]["choices"], ["YEARLY", "MONTHLY", "WEEKLY", "DAILY", "WEEKDAYS", "NEVER"]
        )
        self.assertEqual(by_name["duration_unit"]["choices"], ["MINUTES", "HOURS", "DAYS", "WEEKS"])

    def test_permission_is_inherited_from_the_calendar(self) -> None:
        rule = self._rule()
        self.assertIs(CalendarRuleUpdate().permission_target(rule), rule.calendar)

    def test_snapshot_uses_the_handler_timestamp_shapes(self) -> None:
        rule = self._rule()
        action_type = CalendarRuleUpdate()
        snapshot = action_type.snapshot_values(rule, ["frequency", "interval", "start", "end"])

        self.assertEqual(snapshot["frequency"], "WEEKLY")
        self.assertEqual(snapshot["interval"], 1)
        self.assertEqual(snapshot["start"], int(rule.start.timestamp()))
        self.assertIsNone(snapshot["end"])

        base = action_type.fingerprint(rule)
        rule.duration = 60
        rule.save(update_fields=["duration"])
        self.assertNotEqual(base, action_type.fingerprint(rule))

    def test_execute_merges_over_the_cas_verified_values(self) -> None:
        rule = self._rule()
        action = self._action(
            self._flow(),
            action_type="calendar_rule.update",
            target_uuid=rule.uuid,
            values={"duration": 120, "duration_unit": "MINUTES"},
            base_values={"duration": 0, "duration_unit": "MINUTES"},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types.calendars.RestProxy._execute_sync") as execute_sync:
            # async_to_sync mirrors the production executor: the
            # thread-sensitive ORM work runs on the caller thread
            summary = async_to_sync(CalendarRuleUpdate().execute)(action, request=make_request())

        self.assertIn("updated", summary)
        # The rules PUT is form-shaped: every field travels
        params = execute_sync.call_args[0][2]
        for name in ("name", "comments", "frequency", "start", "end", "interval", "duration", "duration_unit"):
            self.assertIn(name, params)
        # The proposed values win over the merged current ones
        self.assertEqual(params["duration"], 120)
        self.assertEqual(params["frequency"], "WEEKLY")
        # And the target is the rules detail of its calendar
        target = execute_sync.call_args[0][0]
        self.assertIs(target.handler, CalendarRules)
        parent_uuid = execute_sync.call_args[0][3]
        self.assertEqual(parent_uuid, rule.calendar.uuid)
