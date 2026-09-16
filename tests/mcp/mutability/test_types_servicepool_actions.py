"""``servicepool.action.set``: scheduled calendar actions surface.

Rows carry their own uuid as identity (no natural key exists for a
scheduled action), the payload params schema is the one every action
declares in ``CALENDAR_ACTION_DICT``, and the valid action ids are the
pool's live ``actions_list``. Execution diffs delete/create/edit through
the canonical ``services_pools/{uuid}/actions`` detail surface.
"""

import json
import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.types.service_pools.action import ServicePoolActions
from uds.REST.methods.op_calendars import ActionsCalendars
from uds.REST.methods.services_pools import ServicesPools

from tests.fixtures.authenticators import create_db_groups
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


def _calendar(name: str = "actions-test-calendar") -> models.Calendar:
    return models.Calendar.objects.create(name=name, comments="")


def _transport() -> models.Transport:
    from tests.fixtures.services import create_db_transport

    return create_db_transport()


def _action_type() -> typing.Any:
    factory = registry_get("servicepool.action.set")
    assert factory is not None
    return factory()


def _row(
    calendar: models.Calendar,
    action: str = "PUBLISH",
    params: typing.Any = None,
    *,
    uuid: typing.Any = ...,
    at_start: bool = True,
    events_offset: int = -30,
) -> dict[str, typing.Any]:
    row: dict[str, typing.Any] = {
        "calendar_id": calendar.uuid,
        "action": action,
        "at_start": at_start,
        "events_offset": events_offset,
        "params": {} if params is None else params,
    }
    if uuid is not ...:
        row["uuid"] = uuid
    return row


def _scheduled(
    pool: models.ServicePool,
    calendar: models.Calendar,
    action: str = "PUBLISH",
    params: typing.Any = None,
    at_start: bool = True,
    events_offset: int = -30,
) -> models.CalendarAction:
    return models.CalendarAction.objects.create(
        calendar=calendar,
        service_pool=pool,
        action=action,
        at_start=at_start,
        events_offset=events_offset,
        params=json.dumps(params or {}),
    )


class ServicePoolActionsRegistryTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("servicepool.action.set")
        assert found is not None
        self.assertIs(type(found()), ServicePoolActions)
        self.assertIn("servicepool.action.set", all_type_ids())

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            _action_type().resolve_target("00000000-0000-0000-0000-000000000000")

    def test_is_target_scoped(self) -> None:
        self.assertIs(ServicePoolActions.target_scoped_fields, True)

    def test_read_view_publishes_rows_and_available_actions(self) -> None:
        pool = _pool()
        calendar = _calendar()
        row = _scheduled(pool, calendar, "PUBLISH", at_start=False, events_offset=15)
        view = ServicePoolActions().read(pool.uuid)
        self.assertEqual(view["entity_type"], "servicepool.action")
        self.assertEqual(
            view["current_actions"],
            [
                {
                    "uuid": row.uuid,
                    "calendar_id": calendar.uuid,
                    "action": "PUBLISH",
                    "at_start": False,
                    "events_offset": 15,
                    "params": {},
                    "calendar_name": calendar.name,
                    "action_description": "Publish",
                }
            ],
        )
        # The caching test service makes the cache actions available too
        self.assertIn("CACHEL1", view["available_actions"])
        self.assertIn("PUBLISH", view["available_actions"])


class ServicePoolActionsFieldsTest(FlowTestCase):
    def test_single_editlist_field_with_calendar_choices(self) -> None:
        calendar = _calendar("calendar-a")
        defs = _action_type().field_definitions("servicepool")
        self.assertEqual([d["name"] for d in defs], ["actions"])
        field = defs[0]
        self.assertEqual(field["type"], types.ui.FieldType.EDITABLELIST.value)
        self.assertEqual({c["value"]: c["label"] for c in field["choices"]}, {calendar.uuid: calendar.name})
        self.assertEqual(
            sorted(field["row"]),
            ["action", "at_start", "calendar_id", "events_offset", "params", "uuid"],
        )

    def test_tooltip_describes_the_param_shape_of_valid_actions(self) -> None:
        pool = _pool()
        defs = _action_type().field_definitions("servicepool", pool)
        tooltip = defs[0]["tooltip"]
        self.assertIn("PUBLISH", tooltip)
        self.assertIn("size: numeric", tooltip)  # CACHEL1 / INITIAL / MAX
        self.assertIn("transport: transport", tooltip)  # ADD_TRANSPORT
        self.assertIn("group: group", tooltip)  # ADD_GROUP

    def test_valid_complete_set_of_creates(self) -> None:
        pool = _pool()
        calendar = _calendar()
        transport = _transport()
        group = create_db_groups(self.authenticator, 1)[0]
        values = {
            "actions": [
                _row(calendar, "PUBLISH"),
                _row(calendar, "CACHEL1", {"size": 3}),
                _row(calendar, "ADD_TRANSPORT", {"transport": transport.uuid}),
                _row(calendar, "ADD_GROUP", {"group": f"{self.authenticator.uuid}@{group.uuid}"}),
            ]
        }
        self.assertEqual(_action_type().validate_values("servicepool", values, pool), [])
        # Empty set is valid: it means "remove every scheduled action"
        self.assertEqual(_action_type().validate_values("servicepool", {"actions": []}, pool), [])

    def test_edit_rows_reference_live_uuids(self) -> None:
        pool = _pool()
        calendar = _calendar()
        live = _scheduled(pool, calendar, "PUBLISH")
        errors = _action_type().validate_values(
            "servicepool", {"actions": [_row(calendar, "PUBLISH", uuid=live.uuid)]}, pool
        )
        self.assertEqual(errors, [])
        # A uuid that is not a current row of this pool is rejected
        other = _scheduled(_pool(), _calendar("other-calendar"), "PUBLISH")
        errors = _action_type().validate_values(
            "servicepool", {"actions": [_row(calendar, "PUBLISH", uuid=other.uuid)]}, pool
        )
        self.assertTrue(any("does not match a current scheduled action" in e for e in errors))

    def test_missing_actions_field_rejected(self) -> None:
        errors = _action_type().validate_values("servicepool", {"other": 1})
        self.assertTrue(any("unknown fields" in e for e in errors))
        self.assertTrue(any("actions is required" in e for e in errors))

    def test_row_shape_errors(self) -> None:
        pool = _pool()
        calendar = _calendar()
        row = _row(calendar)
        errors = _action_type().validate_values("servicepool", {"actions": [{**row, "extra": 1}]}, pool)
        self.assertTrue(any("unknown keys: extra" in e for e in errors))
        incomplete = {k: v for k, v in row.items() if k != "at_start"}
        errors = _action_type().validate_values("servicepool", {"actions": [incomplete]}, pool)
        self.assertTrue(any("missing keys: at_start" in e for e in errors))
        errors = _action_type().validate_values("servicepool", {"actions": ["not-a-dict"]}, pool)
        self.assertTrue(any("must be an object" in e for e in errors))
        errors = _action_type().validate_values(
            "servicepool", {"actions": [_row(_calendar("ghost"), "PUBLISH", uuid="not-a-uuid")]}, pool
        )
        self.assertTrue(any("not a valid uuid" in e for e in errors))

    def test_unknown_calendar_rejected(self) -> None:
        pool = _pool()
        errors = _action_type().validate_values(
            "servicepool", {"actions": [_row(_calendar(), "PUBLISH")]}, pool
        )
        self.assertEqual(errors, [])  # sanity: existing calendar passes
        errors = _action_type().validate_values(
            "servicepool",
            {
                "actions": [
                    {
                        "calendar_id": "00000000-0000-0000-0000-000000000000",
                        "action": "PUBLISH",
                        "at_start": True,
                        "events_offset": 0,
                        "params": {},
                    }
                ]
            },
            pool,
        )
        self.assertTrue(any("does not match any existing calendar" in e for e in errors))

    def test_action_not_valid_for_the_pool_rejected(self) -> None:
        pool = _pool()
        calendar = _calendar()
        # CLEAN_CACHE_L1 exists in the dictionary but actions_list() never
        # offers it for this pool
        self.assertNotIn("CLEAN_CACHE_L1", ServicePoolActions().read(pool.uuid)["available_actions"])
        errors = _action_type().validate_values(
            "servicepool", {"actions": [_row(calendar, "CLEAN_CACHE_L1")]}, pool
        )
        self.assertTrue(any("is not currently valid for this pool" in e for e in errors))
        # A completely unknown id is rejected too
        errors = _action_type().validate_values("servicepool", {"actions": [_row(calendar, "NONSENSE")]}, pool)
        self.assertTrue(any("must be one of the action ids" in e for e in errors))

    def test_actions_valid_on_live_rows_stay_editable(self) -> None:
        # A scheduling that stopped being valid (e.g. pool locked later)
        # can still be edited/removed while it exists on the pool
        pool = _pool()
        calendar = _calendar()
        live = _scheduled(pool, calendar, "CLEAN_CACHE_L1")
        pool.state = types.states.State.LOCKED
        pool.save(update_fields=["state"])
        available = ServicePoolActions().read(pool.uuid)["available_actions"]
        self.assertNotIn("CLEAN_CACHE_L1", available)
        errors = _action_type().validate_values(
            "servicepool", {"actions": [_row(calendar, "CLEAN_CACHE_L1", uuid=live.uuid)]}, pool
        )
        self.assertEqual(errors, [])
        # But a NEW clean-cache scheduling on a locked pool is refused
        errors = _action_type().validate_values(
            "servicepool", {"actions": [_row(calendar, "CLEAN_CACHE_L1")]}, pool
        )
        self.assertTrue(any("is not currently valid for this pool" in e for e in errors))

    def test_params_validated_per_action_schema(self) -> None:
        pool = _pool()
        calendar = _calendar()
        # numeric param
        errors = _action_type().validate_values(
            "servicepool", {"actions": [_row(calendar, "CACHEL1", {"size": "3"})]}, pool
        )
        self.assertTrue(any("params.size must be an integer" in e for e in errors))
        errors = _action_type().validate_values(
            "servicepool", {"actions": [_row(calendar, "CACHEL1", {})]}, pool
        )
        self.assertTrue(any("params is missing keys: size" in e for e in errors))
        errors = _action_type().validate_values(
            "servicepool", {"actions": [_row(calendar, "CACHEL1", {"size": 1, "bogus": 2})]}, pool
        )
        self.assertTrue(any("params has unknown keys" in e for e in errors))
        # bool param
        errors = _action_type().validate_values(
            "servicepool", {"actions": [_row(calendar, "IGNORE_UNUSED", {"state": "yes"})]}, pool
        )
        self.assertTrue(any("params.state must be a boolean" in e for e in errors))
        # paramless actions must carry an empty object
        errors = _action_type().validate_values(
            "servicepool", {"actions": [_row(calendar, "PUBLISH", {"size": 1})]}, pool
        )
        self.assertTrue(any("params has unknown keys: size" in e for e in errors))

    def test_transport_param_resolves_real_transports(self) -> None:
        pool = _pool()
        calendar = _calendar()
        transport = _transport()
        values = {"actions": [_row(calendar, "ADD_TRANSPORT", {"transport": transport.uuid})]}
        self.assertEqual(_action_type().validate_values("servicepool", values, pool), [])
        values = {
            "actions": [_row(calendar, "ADD_TRANSPORT", {"transport": "00000000-0000-0000-0000-000000000000"})]
        }
        errors = _action_type().validate_values("servicepool", values, pool)
        self.assertTrue(any("does not match any existing transport" in e for e in errors))

    def test_group_param_resolves_authenticator_at_group(self) -> None:
        pool = _pool()
        calendar = _calendar()
        group = create_db_groups(self.authenticator, 1)[0]
        values = {
            "actions": [_row(calendar, "ADD_GROUP", {"group": f"{self.authenticator.uuid}@{group.uuid}"})]
        }
        self.assertEqual(_action_type().validate_values("servicepool", values, pool), [])
        # foreign group (exists but belongs to another authenticator)
        other_auth = models.Authenticator.objects.create(name="other-auth", data_type="LDAP", comments="")
        values = {"actions": [_row(calendar, "ADD_GROUP", {"group": f"{other_auth.uuid}@{group.uuid}"})]}
        errors = _action_type().validate_values("servicepool", values, pool)
        self.assertTrue(any("does not belong to authenticator" in e for e in errors))
        # malformed shapes
        for bad in ("no-at-sign", f"{self.authenticator.uuid}@", f"x@{group.uuid}"):
            values = {"actions": [_row(calendar, "ADD_GROUP", {"group": bad})]}
            errors = _action_type().validate_values("servicepool", values, pool)
            self.assertTrue(errors, f"expected rejection for {bad!r}")


class ServicePoolActionsFingerprintTest(FlowTestCase):
    def test_snapshot_and_fingerprint_track_the_rows(self) -> None:
        pool = _pool()
        calendar = _calendar()
        action_type = _action_type()
        self.assertEqual(action_type.snapshot_values(pool, ["actions"]), {"actions": []})
        base = action_type.fingerprint(pool)
        self.assertEqual(base, action_type.fingerprint(pool))

        row = _scheduled(pool, calendar, "PUBLISH")
        self.assertNotEqual(base, action_type.fingerprint(pool))
        row.events_offset = 90
        row.save(update_fields=["events_offset"])
        self.assertNotEqual(base, action_type.fingerprint(pool))
        row.delete()
        self.assertEqual(base, action_type.fingerprint(pool))


class ServicePoolActionsExecuteTest(FlowTestCase):
    def _run(self, pool: models.ServicePool, rows: list[dict[str, typing.Any]]) -> mock.Mock:
        action = self._action(
            self._flow(),
            action_type="servicepool.action.set",
            target_uuid=pool.uuid,
            values={"actions": rows},
            base_values={},
            base_etag="etag",
        )
        proxy_cls = mock.MagicMock()
        proxy_cls.return_value.execute = mock.AsyncMock(return_value=None)
        with mock.patch("uds.mutability.types.service_pools.action.RestProxy", proxy_cls):
            summary = async_to_sync(_action_type().execute)(action, request=make_request())
        self._last_summary = summary
        return proxy_cls

    @staticmethod
    def _live_rows(pool: models.ServicePool) -> list[dict[str, typing.Any]]:
        from uds.mutability.types.service_pools.action import _action_rows

        return _action_rows(pool)

    def test_diff_applies_delete_create_update_in_order(self) -> None:
        kept_calendar = _calendar("kept")
        dropped = _calendar("dropped")
        added = _calendar("added")
        pool = _pool()
        _scheduled(pool, kept_calendar, "PUBLISH")
        _scheduled(pool, dropped, "PUBLISH")

        live = self._live_rows(pool)
        kept = next(row for row in live if row["calendar_id"] == kept_calendar.uuid)
        kept_changed = {**kept, "at_start": False}
        desired = [
            _row(added, "PUBLISH"),  # create
            kept_changed,  # edit (carries uuid)
        ]

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
            self.assertIs(target.handler, ActionsCalendars)
            self.assertEqual(target.path, "services_pools/{uuid}/actions")
            self.assertIs(target.parent.handler, ServicesPools)
            self.assertEqual(call[1]["parent_uuid"], pool.uuid)

        delete_call = calls[0]
        dropped_uuid = models.CalendarAction.objects.get(service_pool=pool, calendar=dropped).uuid
        self.assertEqual(delete_call[0][0].args, (dropped_uuid,))

        create_call = calls[1]
        self.assertEqual(create_call[0][0].args, ())
        self.assertEqual(
            create_call[0][2],
            {
                "calendar_id": added.uuid,
                "action": "PUBLISH",
                "at_start": True,
                "events_offset": -30,
                "params": {},
            },
        )

        update_call = calls[2]
        kept_uuid = models.CalendarAction.objects.get(service_pool=pool, calendar=kept_calendar).uuid
        self.assertEqual(update_call[0][0].args, (kept_uuid,))
        self.assertEqual(update_call[0][2]["at_start"], False)
        self.assertIn("1 created, 1 modified, 1 removed", self._last_summary)

    def test_equal_set_is_a_noop(self) -> None:
        pool = _pool()
        calendar = _calendar()
        _scheduled(pool, calendar, "PUBLISH")
        _scheduled(pool, calendar, "CACHEL1", {"size": 2})
        proxy_cls = self._run(pool, self._live_rows(pool))
        proxy_cls.return_value.execute.assert_not_awaited()
        self.assertIn("0 created, 0 modified, 0 removed", self._last_summary)

    def test_empty_set_removes_everything(self) -> None:
        pool = _pool()
        calendar = _calendar()
        _scheduled(pool, calendar, "PUBLISH")
        _scheduled(pool, calendar, "CACHEL1", {"size": 2})
        proxy_cls = self._run(pool, [])
        self.assertEqual(proxy_cls.return_value.execute.await_count, 2)
        for call in proxy_cls.return_value.execute.await_args_list:
            self.assertEqual(call[0][0].method, types.rest.CustomMethodMethod.DELETE)
        self.assertIn("2 removed", self._last_summary)

    def test_row_vanished_after_approval_aborts_before_touching_rest(self) -> None:
        pool = _pool()
        calendar = _calendar()
        doomed = _scheduled(pool, calendar, "PUBLISH")
        survivor = _scheduled(pool, calendar, "CACHEL1", {"size": 1})
        rows = self._live_rows(pool)
        doomed_uuid = doomed.uuid
        doomed_row = next(row for row in rows if row["uuid"] == doomed_uuid)
        survivor_row = next(row for row in rows if row["uuid"] == survivor.uuid)
        doomed.delete()  # vanishes between approval and execution
        with self.assertRaises(rest_exceptions.RequestError):
            self._run(pool, [doomed_row, survivor_row])

    def test_params_travel_as_json_object(self) -> None:
        pool = _pool()
        calendar = _calendar()
        transport = _transport()
        row = _row(calendar, "ADD_TRANSPORT", {"transport": transport.uuid})
        proxy_cls = self._run(pool, [row])
        call = proxy_cls.return_value.execute.await_args_list[0]
        self.assertEqual(call[0][2]["params"], {"transport": transport.uuid})
        self.assertEqual(call[0][2]["action"], "ADD_TRANSPORT")
