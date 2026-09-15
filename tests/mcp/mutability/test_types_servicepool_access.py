"""``servicepool.access.set`` / ``servicepool.fallback.update``: access surface.

The rules family mirrors ``metapool.member`` (rows with per-row data, keyed
by the natural key: the calendar), the fallback is a scalar family executed
through the master custom POST ``services_pools/{uuid}/fallback_access``.
"""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.types.service_pools.access import ServicePoolAccess
from uds.mutability.types.service_pools.fallback import ServicePoolFallback
from uds.REST.methods.op_calendars import AccessCalendars
from uds.REST.methods.services_pools import ServicesPools

from tests.mcp.mutability._helpers import FlowTestCase, make_request


def _pool() -> models.ServicePool:
    from tests.fixtures.services import (
        create_db_osmanager,
        create_db_provider,
        create_db_service,
        create_db_servicepool,
    )

    service = create_db_service(create_db_provider())
    return create_db_servicepool(service=service, osmanager=create_db_osmanager())


def _calendar(name: str = "access-test-calendar") -> models.Calendar:
    return models.Calendar.objects.create(name=name, comments="")


def _access() -> typing.Any:
    factory = registry_get("servicepool.access.set")
    assert factory is not None
    return factory()


def _fallback() -> typing.Any:
    factory = registry_get("servicepool.fallback.update")
    assert factory is not None
    return factory()


class ServicePoolAccessRegistryTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("servicepool.access.set")
        assert found is not None
        self.assertIs(type(found()), ServicePoolAccess)
        self.assertIn("servicepool.access.set", all_type_ids())

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            _access().resolve_target("00000000-0000-0000-0000-000000000000")

    def test_is_target_scoped(self) -> None:
        self.assertIs(ServicePoolAccess.target_scoped_fields, True)

    def test_read_view_publishes_rules(self) -> None:
        pool = _pool()
        calendar = _calendar()
        models.CalendarAccess.objects.create(service_pool=pool, calendar=calendar, access="ALLOW", priority=3)
        # The read generator instantiates the family unbound (reads are
        # not operations).
        view = ServicePoolAccess().read(pool.uuid)
        self.assertEqual(view["entity_type"], "servicepool.access")
        self.assertEqual(
            view["current_rules"],
            [{"calendar_id": calendar.uuid, "access": "ALLOW", "priority": 3, "calendar_name": calendar.name}],
        )

    def test_write_only_family_is_servicepool_fallback(self) -> None:
        # The fallback current value is already published by the curated
        # get_servicepool_fallback_access tool: no generated read for it.
        self.assertFalse(ServicePoolFallback.readable())
        self.assertTrue(ServicePoolAccess.readable())


class ServicePoolAccessFieldsTest(FlowTestCase):
    def test_single_editlist_field_with_calendar_choices(self) -> None:
        calendar_a = _calendar("calendar-a")
        calendar_b = _calendar("calendar-b")
        defs = _access().field_definitions("servicepool")
        self.assertEqual([d["name"] for d in defs], ["calendars"])
        calendars_def = defs[0]
        self.assertEqual(calendars_def["type"], types.ui.FieldType.EDITABLELIST.value)
        choices = {c["value"]: c["label"] for c in calendars_def["choices"]}
        self.assertEqual(choices[calendar_a.uuid], calendar_a.name)
        self.assertEqual(choices[calendar_b.uuid], calendar_b.name)

    def test_valid_complete_set(self) -> None:
        pool = _pool()
        calendar = _calendar()
        models.CalendarAccess.objects.create(service_pool=pool, calendar=calendar, access="DENY", priority=1)
        rows = [{"calendar_id": calendar.uuid, "access": "DENY", "priority": 1}]
        self.assertEqual(_access().validate_values("servicepool", {"calendars": rows}, pool), [])
        # Empty set is valid: it means "remove every rule"
        self.assertEqual(_access().validate_values("servicepool", {"calendars": []}, pool), [])

    def test_missing_calendars_field_rejected(self) -> None:
        errors = _access().validate_values("servicepool", {"other": 1})
        self.assertTrue(any("unknown fields" in e for e in errors))
        self.assertTrue(any("calendars is required" in e for e in errors))

    def test_row_shape_errors(self) -> None:
        calendar = _calendar()
        base = {"calendar_id": calendar.uuid, "access": "ALLOW", "priority": 0}
        bad_rows: list[tuple[dict[str, typing.Any], str]] = [
            ({**base, "extra": 1}, "unknown keys"),
            ({"calendar_id": calendar.uuid, "access": "ALLOW"}, "missing keys"),
            ({**base, "calendar_id": "no-uuid"}, "not a valid uuid"),
            (
                {**base, "calendar_id": "00000000-0000-0000-0000-000000000000"},
                "does not match any existing calendar",
            ),
            ({**base, "access": "MAYBE"}, "access must be ALLOW or DENY"),
            ({**base, "access": "allow"}, "access must be ALLOW or DENY"),
            ({**base, "priority": "high"}, "priority must be an integer"),
            ({**base, "priority": True}, "priority must be an integer"),
        ]
        for row, fragment in bad_rows:
            errors = _access().validate_values("servicepool", {"calendars": [row]})
            self.assertTrue(any(fragment in e for e in errors), f"{row!r} -> {errors}")

    def test_duplicate_calendar_id_rejected(self) -> None:
        calendar = _calendar()
        row = {"calendar_id": calendar.uuid, "access": "ALLOW", "priority": 0}
        errors = _access().validate_values("servicepool", {"calendars": [row, dict(row)]})
        self.assertTrue(any("duplicates calendar_id" in e for e in errors))

    def test_case_insensitive_uuid_is_normalized(self) -> None:
        calendar = _calendar()
        errors = _access().validate_values(
            "servicepool",
            {"calendars": [{"calendar_id": calendar.uuid.upper(), "access": "ALLOW", "priority": 0}]},
        )
        self.assertEqual(errors, [])

    def test_live_duplicated_rows_rejected(self) -> None:
        pool = _pool()
        calendar = _calendar()
        models.CalendarAccess.objects.create(service_pool=pool, calendar=calendar, access="ALLOW", priority=0)
        models.CalendarAccess.objects.create(service_pool=pool, calendar=calendar, access="DENY", priority=7)
        rows = [{"calendar_id": calendar.uuid, "access": "ALLOW", "priority": 0}]
        errors = _access().validate_values("servicepool", {"calendars": rows}, pool)
        self.assertTrue(any("duplicated access rules" in e for e in errors))


class ServicePoolAccessCasTest(FlowTestCase):
    def test_snapshot_canonical_rows(self) -> None:
        pool = _pool()
        calendar_a = _calendar("calendar-a")
        calendar_b = _calendar("calendar-b")
        models.CalendarAccess.objects.create(service_pool=pool, calendar=calendar_a, access="ALLOW", priority=1)
        models.CalendarAccess.objects.create(service_pool=pool, calendar=calendar_b, access="DENY", priority=0)
        snapshot = _access().snapshot_values(pool, ["calendars"])
        expected = sorted(
            [
                {"calendar_id": calendar_a.uuid, "access": "ALLOW", "priority": 1},
                {"calendar_id": calendar_b.uuid, "access": "DENY", "priority": 0},
            ],
            key=lambda row: typing.cast(str, row["calendar_id"]),
        )
        self.assertEqual(snapshot["calendars"], expected)

    def test_fingerprint_tracks_rules(self) -> None:
        pool = _pool()
        calendar = _calendar()
        models.CalendarAccess.objects.create(service_pool=pool, calendar=calendar, access="ALLOW", priority=0)
        action_type = _access()

        base = action_type.fingerprint(pool)
        self.assertEqual(base, action_type.fingerprint(pool))

        rule = pool.calendarAccess.first()
        assert rule is not None
        rule.access = "DENY"
        rule.save(update_fields=["access"])
        self.assertNotEqual(base, action_type.fingerprint(pool))


class ServicePoolAccessExecuteTest(FlowTestCase):
    def _run(self, pool: models.ServicePool, calendars: list[dict[str, typing.Any]]) -> mock.Mock:
        action = self._action(
            self._flow(),
            action_type="servicepool.access.set",
            target_uuid=pool.uuid,
            values={"calendars": calendars},
            base_values={},
            base_etag="etag",
        )
        proxy_cls = mock.MagicMock()
        proxy_cls.return_value.execute = mock.AsyncMock(return_value=None)
        with mock.patch("uds.mutability.types.service_pools.access.RestProxy", proxy_cls):
            summary = async_to_sync(_access().execute)(action, request=make_request())
        self._last_summary = summary
        return proxy_cls

    @staticmethod
    def _rows(pool: models.ServicePool) -> list[dict[str, typing.Any]]:
        return [
            {"calendar_id": rule.calendar.uuid, "access": rule.access, "priority": rule.priority}
            for rule in pool.calendarAccess.all()
        ]

    def test_diff_applies_delete_create_update_in_order(self) -> None:
        kept = _calendar("kept")
        dropped = _calendar("dropped")
        added = _calendar("added")
        pool = _pool()
        models.CalendarAccess.objects.create(service_pool=pool, calendar=kept, access="ALLOW", priority=0)
        models.CalendarAccess.objects.create(service_pool=pool, calendar=dropped, access="DENY", priority=0)

        rows = self._rows(pool)
        kept_row = next(row for row in rows if row["calendar_id"] == kept.uuid)
        kept_row["priority"] = 5  # update
        desired = [kept_row, {"calendar_id": added.uuid, "access": "ALLOW", "priority": 1}]

        proxy_cls = self._run(pool, desired)

        calls = proxy_cls.return_value.execute.await_args_list
        verbs = [call[0][0].method for call in calls]
        self.assertEqual(
            verbs,
            [
                types.rest.CustomMethodMethod.DELETE,  # dropped first
                types.rest.CustomMethodMethod.POST,  # then added
                types.rest.CustomMethodMethod.PUT,  # then modified
            ],
        )
        for call in calls:
            target = call[0][0]
            self.assertIs(target.handler, AccessCalendars)
            self.assertEqual(target.path, "services_pools/{uuid}/access")
            self.assertIs(target.parent.handler, ServicesPools)
            self.assertEqual(call[1]["parent_uuid"], pool.uuid)

        delete_call = calls[0]
        dropped_rule_uuid = models.CalendarAccess.objects.get(service_pool=pool, calendar=dropped).uuid
        self.assertEqual(delete_call[0][0].args, (dropped_rule_uuid,))
        self.assertEqual(delete_call[0][2], {})

        create_call = calls[1]
        self.assertEqual(create_call[0][0].args, ())
        self.assertEqual(create_call[0][2], {"calendar_id": added.uuid, "access": "ALLOW", "priority": 1})

        update_call = calls[2]
        kept_rule_uuid = models.CalendarAccess.objects.get(service_pool=pool, calendar=kept).uuid
        self.assertEqual(update_call[0][0].args, (kept_rule_uuid,))
        self.assertEqual(update_call[0][2], {"calendar_id": kept.uuid, "access": "ALLOW", "priority": 5})

        self.assertIn("1 created, 1 modified, 1 removed", self._last_summary)

    def test_equal_set_is_a_noop(self) -> None:
        pool = _pool()
        calendar_a = _calendar("calendar-a")
        calendar_b = _calendar("calendar-b")
        models.CalendarAccess.objects.create(service_pool=pool, calendar=calendar_a, access="ALLOW", priority=0)
        models.CalendarAccess.objects.create(service_pool=pool, calendar=calendar_b, access="DENY", priority=1)
        proxy_cls = self._run(pool, self._rows(pool))
        proxy_cls.return_value.execute.assert_not_awaited()
        self.assertIn("0 created, 0 modified, 0 removed", self._last_summary)

    def test_empty_set_removes_everything(self) -> None:
        pool = _pool()
        models.CalendarAccess.objects.create(
            service_pool=pool, calendar=_calendar("calendar-a"), access="ALLOW"
        )
        models.CalendarAccess.objects.create(service_pool=pool, calendar=_calendar("calendar-b"), access="DENY")
        proxy_cls = self._run(pool, [])
        self.assertEqual(proxy_cls.return_value.execute.await_count, 2)
        for call in proxy_cls.return_value.execute.await_args_list:
            self.assertEqual(call[0][0].method, types.rest.CustomMethodMethod.DELETE)
        self.assertIn("2 removed", self._last_summary)

    def test_live_duplicated_rows_abort_before_touching_rest(self) -> None:
        pool = _pool()
        calendar = _calendar()
        models.CalendarAccess.objects.create(service_pool=pool, calendar=calendar, access="ALLOW", priority=0)
        models.CalendarAccess.objects.create(service_pool=pool, calendar=calendar, access="DENY", priority=7)
        with self.assertRaises(rest_exceptions.RequestError):
            self._run(pool, [{"calendar_id": calendar.uuid, "access": "ALLOW", "priority": 0}])


class ServicePoolFallbackTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("servicepool.fallback.update")
        assert found is not None
        self.assertIs(type(found()), ServicePoolFallback)
        self.assertIn("servicepool.fallback.update", all_type_ids())

    def test_single_choice_field(self) -> None:
        defs = _fallback().field_definitions("servicepool")
        self.assertEqual([d["name"] for d in defs], ["fallback"])
        self.assertEqual(defs[0]["type"], types.ui.FieldType.CHOICE.value)
        self.assertEqual({c["value"] for c in defs[0]["choices"]}, {"ALLOW", "DENY"})

    def test_valid_values(self) -> None:
        pool = _pool()
        self.assertEqual(_fallback().validate_values("servicepool", {"fallback": "DENY"}, pool), [])
        self.assertEqual(_fallback().validate_values("servicepool", {"fallback": "ALLOW"}, pool), [])

    def test_invalid_values(self) -> None:
        errors = _fallback().validate_values("servicepool", {})
        self.assertTrue(any("fallback is required" in e for e in errors))
        errors = _fallback().validate_values("servicepool", {"fallback": "MAYBE"})
        self.assertTrue(any("must be ALLOW or DENY" in e for e in errors))

    def test_snapshot_and_fingerprint_track_the_scalar(self) -> None:
        pool = _pool()
        action_type = _fallback()
        self.assertEqual(action_type.snapshot_values(pool, ["fallback"]), {"fallback": pool.fallbackAccess})
        base = action_type.fingerprint(pool)
        pool.fallbackAccess = "DENY"
        pool.save(update_fields=["fallbackAccess"])
        self.assertNotEqual(base, action_type.fingerprint(pool))

    def test_execute_posts_the_master_custom_method(self) -> None:
        pool = _pool()
        action = self._action(
            self._flow(),
            action_type="servicepool.fallback.update",
            target_uuid=pool.uuid,
            values={"fallback": "DENY"},
            base_values={"fallback": "ALLOW"},
            base_etag="etag",
        )
        proxy_cls = mock.MagicMock()
        proxy_cls.return_value.execute = mock.AsyncMock(return_value="DENY")
        with mock.patch("uds.mutability.types.service_pools.fallback.RestProxy", proxy_cls):
            summary = async_to_sync(_fallback().execute)(action, request=make_request())

        call = proxy_cls.return_value.execute.await_args_list[0]
        target = call[0][0]
        self.assertIs(target.handler, ServicesPools)
        self.assertEqual(target.path, "services_pools")
        self.assertEqual(target.method, types.rest.CustomMethodMethod.POST)
        self.assertEqual(target.args, (pool.uuid, "fallback_access"))
        self.assertEqual(call[0][2], {"fallback_access": "DENY"})
        self.assertIn("fallback access set to DENY", summary)
