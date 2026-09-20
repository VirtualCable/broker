"""``calendar`` and ``calendar_rule`` (create / update / delete): registration,
discovery, CAS (timestamps included) and execution shapes."""

import datetime
import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.consts.mcp import CREATE_TARGET_UUID
from uds.core.exceptions import rest as rest_exceptions
from uds.mcp.rest_proxy import RestProxy
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.base import ActionOperation
from uds.mutability.types.calendars import CalendarUpdate
from uds.mutability.types.calendars.rule import CalendarRuleUpdate
from uds.models import Calendar, CalendarRule
from uds.REST.methods.calendars import Calendars
from uds.REST.methods.calendarrules import CalendarRuleItem, CalendarRules

from tests.mcp.mutability._helpers import FlowTestCase, build_action, make_request

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


def _calendar_bound(operation: str) -> CalendarUpdate:
    factory = registry_get(f"calendar.{operation}")
    assert factory is not None
    return typing.cast(CalendarUpdate, factory())


def _rule_bound(operation: str) -> CalendarRuleUpdate:
    factory = registry_get(f"calendar_rule.{operation}")
    assert factory is not None
    return typing.cast(CalendarRuleUpdate, factory())


class CalendarUpdateTest(FlowTestCase):
    def test_calendar_update_is_registered(self) -> None:
        found = registry_get("calendar.update")
        assert found is not None
        self.assertIs(type(found()), CalendarUpdate)
        self.assertIn("calendar.update", all_type_ids())
        self.assertIs(type(_calendar_bound("create")), CalendarUpdate)
        self.assertIs(type(_calendar_bound("delete")), CalendarUpdate)
        self.assertEqual(
            CalendarUpdate.supported_operations(),
            frozenset({ActionOperation.UPDATE, ActionOperation.CREATE, ActionOperation.DELETE}),
        )

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


class CalendarCreateDeleteTest(FlowTestCase):
    """calendar.create / calendar.delete: validation and POST/DELETE shapes."""

    def test_create_requires_name(self) -> None:
        create = _calendar_bound("create")
        self.assertTrue(any("name" in e for e in create.create_validate_values("calendar", {})))
        self.assertEqual(create.create_validate_values("calendar", {"name": "holidays"}), [])

    def test_delete_publishes_no_fields(self) -> None:
        delete = _calendar_bound("delete")
        self.assertEqual(delete.field_definitions("calendar"), [])
        self.assertEqual(delete.validate_values("calendar", {}, None), [])
        self.assertIn("Propose deleting a calendar", delete.tool_title())
        self.assertIn("rules", delete.tool_description())

    def test_create_execution_posts_the_form_shape(self) -> None:
        action = build_action(
            action_type="calendar.create",
            target_uuid=CREATE_TARGET_UUID,
            values={"name": "new calendar"},
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value={"id": "cal-uuid"})
            summary = async_to_sync(_calendar_bound("create").execute)(action, request=make_request())
            target, _request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual(
                (target.handler, target.path, target.method.value, target.args),
                (Calendars, "calendars", "POST", ()),
            )
        self.assertEqual(params, {"name": "new calendar", "comments": "", "tags": []})
        self.assertIn("created", summary)
        self.assertIn("cal-uuid", summary)

    def test_delete_executes_the_rest_call(self) -> None:
        calendar = Calendar.objects.create(name="work days")
        action = self._action(
            self._flow(),
            action_type="calendar.delete",
            target_uuid=calendar.uuid,
            values={},
            base_values={},
            base_etag="etag",
        )
        with mock.patch("uds.mutability.verbs.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            summary = async_to_sync(_calendar_bound("delete").execute)(action, request=make_request())
            target, _request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual(
                (target.handler, target.path, target.method.value, target.args),
                (Calendars, "calendars", "DELETE", (calendar.uuid,)),
            )
            self.assertEqual(params, {})
        self.assertIn("deleted", summary)
        self.assertIn("work days", summary)


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
        assert found is not None
        self.assertIs(type(found()), CalendarRuleUpdate)
        self.assertIn("calendar_rule.update", all_type_ids())
        self.assertIs(type(_rule_bound("create")), CalendarRuleUpdate)
        self.assertIs(type(_rule_bound("delete")), CalendarRuleUpdate)
        self.assertEqual(
            CalendarRuleUpdate.supported_operations(),
            frozenset({ActionOperation.UPDATE, ActionOperation.CREATE, ActionOperation.DELETE}),
        )

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

        with mock.patch("uds.mutability.types.calendars.rule.RestProxy._execute_sync") as execute_sync:
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


class CalendarRuleCreateTest(FlowTestCase):
    """calendar_rule.create: parent targeting, validation, POST shape."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.calendar = Calendar.objects.create(name="holidays")

    def test_creation_targets_the_calendar(self) -> None:
        self.assertTrue(CalendarRuleUpdate.create_needs_parent)
        self.assertFalse(CalendarRuleUpdate.create_has_gallery)
        self.assertEqual(_rule_bound("create").full_id, "calendar_rule.create")
        resolved = _rule_bound("create").resolve_create_parent(self.calendar.uuid)
        self.assertEqual(resolved.pk, self.calendar.pk)  # a fresh fetch, not the same instance
        with self.assertRaises(rest_exceptions.NotFound):
            _rule_bound("create").resolve_create_parent("00000000-0000-0000-0000-000000000000")

    def test_creation_view_offers_the_form_fields(self) -> None:
        names = [d["name"] for d in _rule_bound("create").create_field_definitions("calendar_rule")]
        self.assertEqual(
            names,
            ["name", "comments", "frequency", "start", "end", "interval", "duration", "duration_unit"],
        )

    def test_create_requires_the_handler_read_fields(self) -> None:
        create = _rule_bound("create")
        errors = create.create_validate_values("calendar_rule", {})
        for field in ("name", "frequency", "start"):
            self.assertTrue(any(field in e for e in errors), field)
        self.assertEqual(
            create.create_validate_values("calendar_rule", {"name": "r", "frequency": "WEEKLY", "start": 1}),
            [],
        )
        # out-of-universe choices and non-numeric timestamps still fail
        self.assertTrue(
            any(
                "frequency" in e
                for e in create.create_validate_values(
                    "calendar_rule", {"name": "r", "frequency": "HOURLY", "start": 1}
                )
            )
        )
        self.assertTrue(
            any(
                "start" in e
                for e in create.create_validate_values(
                    "calendar_rule", {"name": "r", "frequency": "WEEKLY", "start": "soon"}
                )
            )
        )

    def test_the_end_field_accepts_the_null_sentinel(self) -> None:
        # "no end" travels as null exactly like the admin form sends it
        create = _rule_bound("create")
        self.assertEqual(
            create.create_validate_values(
                "calendar_rule", {"name": "r", "frequency": "WEEKLY", "start": 1, "end": None}
            ),
            [],
        )
        update = _rule_bound("update")
        self.assertEqual(update.validate_values("calendar_rule", {"end": None}), [])

    def test_execution_posts_the_detail_with_parent_uuid(self) -> None:
        action = build_action(
            action_type="calendar_rule.create",
            target_uuid=self.calendar.uuid,
            values={"name": "easter", "frequency": "YEARLY", "start": 1_777_000_000},
            base_values={},
            base_etag="",
        )
        rule_uuid = "11111111-2222-3333-4444-555555555555"
        with mock.patch.object(RestProxy, "_execute_sync") as execute_sync:
            # detail writes answer with the saved item itself (the handler's
            # ``CalendarRuleItem``, before any JSON rendering)
            execute_sync.return_value = CalendarRuleItem(
                id=rule_uuid,
                name="easter",
                comments="",
                start=datetime.datetime(2026, 4, 5, 0, 0, tzinfo=datetime.timezone.utc),
                end=None,
                frequency="YEARLY",
                interval=1,
                duration=0,
                duration_unit="MINUTES",
                permission=96,
            )
            summary = async_to_sync(_rule_bound("create").execute)(action, request=make_request())

        target, _request, params, parent_uuid = execute_sync.call_args[0]
        self.assertIs(target.handler, CalendarRules)
        self.assertIs(target.parent.handler, Calendars)
        self.assertEqual(target.method.value, "POST")
        self.assertEqual(target.args, ())
        self.assertEqual(parent_uuid, self.calendar.uuid)
        # form-shaped payload: every save_item key rides, the optional ones
        # falling back to the admin "new" defaults
        self.assertEqual(
            params,
            {
                "name": "easter",
                "comments": "",
                "frequency": "YEARLY",
                "start": 1_777_000_000,
                "end": None,
                "interval": 1,
                "duration": 0,
                "duration_unit": "MINUTES",
            },
        )
        self.assertIn("created", summary)
        self.assertIn("easter", summary)
        self.assertIn(rule_uuid, summary)


class CalendarRuleDeleteTest(FlowTestCase):
    @typing.override
    def setUp(self) -> None:
        super().setUp()
        calendar = Calendar.objects.create(name="holidays")
        self.rule = CalendarRule.objects.create(
            calendar=calendar,
            name="christmas",
            comments="",
            start=datetime.datetime(2026, 12, 24, 0, 0, tzinfo=datetime.timezone.utc),
            frequency="WEEKLY",
        )

    def test_delete_publishes_no_fields(self) -> None:
        delete = _rule_bound("delete")
        self.assertEqual(delete.field_definitions("calendar_rule"), [])
        self.assertEqual(delete.validate_values("calendar_rule", {}, None), [])
        self.assertIn("Propose deleting a calendar rule", delete.tool_title())
        self.assertIn("calendar survives", delete.tool_description())
        # the whole-item fingerprint stays operation-independent
        self.assertEqual(
            delete.etag_fields("calendar_rule"),
            [
                "name",
                "comments",
                "frequency",
                "start",
                "end",
                "interval",
                "duration",
                "duration_unit",
            ],
        )

    def test_delete_executes_the_detail_delete(self) -> None:
        action = self._action(
            self._flow(),
            action_type="calendar_rule.delete",
            target_uuid=self.rule.uuid,
            values={},
            base_values={},
            base_etag="etag",
        )
        with mock.patch.object(RestProxy, "_execute_sync") as execute_sync:
            execute_sync.return_value = None
            summary = async_to_sync(_rule_bound("delete").execute)(action, request=make_request())

        target, _request, params, parent_uuid = execute_sync.call_args[0]
        self.assertIs(target.handler, CalendarRules)
        self.assertIs(target.parent.handler, Calendars)
        self.assertEqual(target.method.value, "DELETE")
        self.assertEqual(target.args, (self.rule.uuid,))
        self.assertEqual(parent_uuid, self.rule.calendar.uuid)
        self.assertEqual(params, {})
        self.assertIn("deleted", summary)
        self.assertIn("christmas", summary)

    def test_delete_missing_target_is_not_found(self) -> None:
        action = self._action(
            self._flow(),
            action_type="calendar_rule.delete",
            target_uuid="00000000-0000-0000-0000-000000000000",
            values={},
            base_values={},
            base_etag="etag",
        )
        with self.assertRaises(rest_exceptions.NotFound):
            async_to_sync(_rule_bound("delete").execute)(action, request=make_request())
