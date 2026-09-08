# pyright: reportUnknownLambdaType=false
"""Tests for the mutability utilities (actions lifecycle, store, etag)."""

import datetime
import typing
import unittest

from uds.mcp.mutability import (
    MAX_PENDING_PER_USER,
    PENDING_TTL,
    ActionStatus,
    InvalidTransition,
    NotActionOwner,
    PendingAction,
    PendingActionStore,
    item_etag,
)

from tests.utils.test import UDSTestCase

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


def _action(**kwargs: typing.Any) -> PendingAction:
    """Build a minimal pending action for tests."""
    return PendingAction.create(
        type_id=kwargs.get("type_id", "provider.update"),
        owner_uuid=kwargs.get("owner_uuid", "owner-1"),
        agent=kwargs.get("agent", "test-agent/1.0"),
        target_uuid=kwargs.get("target_uuid", "target-1"),
        values=kwargs.get("values", {"name": "new name"}),
        base_values=kwargs.get("base_values", {"name": "old name"}),
        base_etag=kwargs.get("base_etag", "abc123"),
    )


class PendingActionLifecycle(UDSTestCase):
    """Strict state machine: valid transitions and rejected ones."""

    def test_created_is_pending(self) -> None:
        action = _action()
        self.assertEqual(action.status, ActionStatus.PENDING)
        self.assertFalse(action.status.is_terminal)

    def test_approve_records_admin(self) -> None:
        action = _action()
        action.approve(admin="admin-1")
        self.assertEqual(action.status, ActionStatus.APPROVED)
        self.assertEqual(action.decided_by, "admin-1")
        self.assertIsNotNone(action.decided_at)
        self.assertTrue(action.status.is_terminal)

    def test_reject_records_reason(self) -> None:
        action = _action()
        action.reject(admin="admin-1", reason="not now")
        self.assertEqual(action.status, ActionStatus.REJECTED)
        self.assertEqual(action.result, "not now")

    def test_cancel_by_owner(self) -> None:
        action = _action()
        action.cancel(actor_uuid="owner-1")
        self.assertEqual(action.status, ActionStatus.CANCELLED)

    def test_cancel_by_other_user_raises(self) -> None:
        action = _action()
        with self.assertRaises(NotActionOwner):
            action.cancel(actor_uuid="not-the-owner")

    def test_expire(self) -> None:
        action = _action()
        action.expire()
        self.assertEqual(action.status, ActionStatus.EXPIRED)
        self.assertIsNone(action.decided_by)

    def test_set_result_on_approved(self) -> None:
        action = _action()
        action.approve(admin="admin-1")
        action.set_result(result="done, pool updated")
        self.assertEqual(action.status, ActionStatus.APPROVED)
        self.assertEqual(action.result, "done, pool updated")

    def test_mark_failed_on_approved(self) -> None:
        action = _action()
        action.approve(admin="admin-1")
        action.mark_failed(result="target vanished")
        self.assertEqual(action.status, ActionStatus.FAILED)
        self.assertEqual(action.result, "target vanished")

    def test_decided_is_terminal(self) -> None:
        for decide in (
            lambda a: a.approve(admin="admin-1"),
            lambda a: a.reject(admin="admin-1"),
            lambda a: a.cancel(actor_uuid="owner-1"),
            lambda a: a.expire(),
        ):
            action = _action()
            decide(action)
            with self.assertRaises(InvalidTransition):
                action.approve(admin="admin-1")
            with self.assertRaises(InvalidTransition):
                action.reject(admin="admin-1")
            with self.assertRaises(InvalidTransition):
                action.cancel(actor_uuid="owner-1")
            with self.assertRaises(InvalidTransition):
                action.expire()

    def test_failed_cannot_be_decided_again(self) -> None:
        action = _action()
        action.approve(admin="admin-1")
        action.mark_failed(result="boom")
        with self.assertRaises(InvalidTransition):
            action.set_result(result="x")
        with self.assertRaises(InvalidTransition):
            action.mark_failed(result="boom again")

    def test_serialization_roundtrip(self) -> None:
        action = _action()
        action.approve(admin="admin-1")
        action.set_result(result="ok")
        clone = PendingAction.from_json(action.to_json())
        self.assertEqual(clone.id, action.id)
        self.assertEqual(clone.type_id, action.type_id)
        self.assertEqual(clone.owner_uuid, action.owner_uuid)
        self.assertEqual(clone.values, action.values)
        self.assertEqual(clone.base_values, action.base_values)
        self.assertEqual(clone.base_etag, action.base_etag)
        self.assertEqual(clone.status, action.status)
        self.assertEqual(clone.decided_by, action.decided_by)
        self.assertEqual(clone.decided_at, action.decided_at)
        self.assertEqual(clone.created, action.created)
        self.assertEqual(clone.updated, action.updated)
        self.assertEqual(clone.result, action.result)

    def test_serialization_of_fresh_action(self) -> None:
        action = _action()
        clone = PendingAction.from_json(action.to_json())
        self.assertEqual(clone.status, ActionStatus.PENDING)
        self.assertIsNone(clone.decided_at)
        self.assertIsNone(clone.result)


class PendingActionStoreTest(UDSTestCase):
    """Storage-backed persistence, listing filters and lazy expiry."""

    @typing.override
    def setUp(self) -> None:
        self.store = PendingActionStore()

    @typing.override
    def tearDown(self) -> None:
        for action in self.store.list():
            self.store.delete(action.id)

    def test_save_and_get(self) -> None:
        action = _action()
        self.store.save(action)
        loaded = self.store.get(action.id)
        assert loaded is not None
        self.assertEqual(loaded.id, action.id)
        self.assertEqual(loaded.status, ActionStatus.PENDING)
        self.assertEqual(loaded.values, action.values)

    def test_get_missing_returns_none(self) -> None:
        self.assertIsNone(self.store.get("nonexistent-id"))

    def test_list_by_status(self) -> None:
        approved = _action(values={"name": "approved"})
        approved.approve(admin="admin-1")
        self.store.save(approved)
        self.store.save(_action(values={"name": "pending"}))

        pending = self.store.list(status=ActionStatus.PENDING)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].values, {"name": "pending"})

        approved_list = self.store.list(status=ActionStatus.APPROVED)
        self.assertEqual(len(approved_list), 1)
        self.assertEqual(approved_list[0].values, {"name": "approved"})

    def test_list_by_owner(self) -> None:
        self.store.save(_action(owner_uuid="owner-1"))
        self.store.save(_action(owner_uuid="owner-2"))

        mine = self.store.list(owner_uuid="owner-1")
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0].owner_uuid, "owner-1")

    def test_list_by_owner_and_status(self) -> None:
        for owner in ("owner-1", "owner-2"):
            action = _action(owner_uuid=owner)
            self.store.save(action)
            action = _action(owner_uuid=owner)
            action.approve(admin="admin-1")
            self.store.save(action)

        pending = self.store.list(status=ActionStatus.PENDING, owner_uuid="owner-2")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].owner_uuid, "owner-2")

    def test_count_pending_respects_cap_constant(self) -> None:
        self.assertGreater(MAX_PENDING_PER_USER, 0)
        for i in range(3):
            self.store.save(_action(owner_uuid="owner-1", values={"n": i}))
        self.store.save(_action(owner_uuid="owner-2"))
        self.assertEqual(self.store.count_pending(owner_uuid="owner-1"), 3)
        self.assertEqual(self.store.count_pending(owner_uuid="owner-2"), 1)

    def test_lazy_expiry(self) -> None:
        action = _action()
        action.created = datetime.datetime.now(datetime.UTC) - PENDING_TTL - datetime.timedelta(minutes=1)
        self.store.save(action)

        # Listing pending expires it on the fly and skips it
        self.assertEqual(self.store.list(status=ActionStatus.PENDING), [])

        loaded = self.store.get(action.id)
        assert loaded is not None
        self.assertEqual(loaded.status, ActionStatus.EXPIRED)

        # ... and it is listed as expired from now on
        expired = self.store.list(status=ActionStatus.EXPIRED)
        self.assertEqual(len(expired), 1)

    def test_fresh_pending_is_not_expired(self) -> None:
        action = _action()
        self.store.save(action)
        loaded = self.store.get(action.id)
        assert loaded is not None
        self.assertEqual(loaded.status, ActionStatus.PENDING)

    def test_delete(self) -> None:
        action = _action()
        self.store.save(action)
        self.store.delete(action.id)
        self.assertIsNone(self.store.get(action.id))


class ItemEtagTest(unittest.TestCase):
    """Deterministic fingerprints (CAS base_etag support)."""

    def test_is_stable(self) -> None:
        item = {"name": "pool", "comments": "x", "instance": {"cpu": 2}}
        self.assertEqual(item_etag(item, ["name", "comments"]), item_etag(item, ["name", "comments"]))

    def test_field_order_is_irrelevant(self) -> None:
        item = {"name": "pool", "comments": "x"}
        self.assertEqual(item_etag(item, ["name", "comments"]), item_etag(item, ["comments", "name"]))

    def test_changes_when_value_changes(self) -> None:
        fields = ["name", "cache_l1"]
        old = item_etag({"name": "p", "cache_l1": 2}, fields)
        new = item_etag({"name": "p", "cache_l1": 5}, fields)
        self.assertNotEqual(old, new)

    def test_ignores_unlisted_fields(self) -> None:
        fields = ["name"]
        base = item_etag({"name": "p", "other": 1}, fields)
        changed = item_etag({"name": "p", "other": 999}, fields)
        self.assertEqual(base, changed)

    def test_dot_paths(self) -> None:
        item = {"instance": {"cpu": {"low": 1}}, "name": "x"}
        self.assertEqual(item_etag(item, ["instance.cpu.low"]), item_etag(item, ["instance.cpu.low"]))

    def test_missing_field_behaves_like_empty(self) -> None:
        self.assertEqual(
            item_etag({"name": "p"}, ["name", "nonexistent"]),
            item_etag({"name": "p", "nonexistent": ""}, ["name", "nonexistent"]),
        )


if __name__ == "__main__":
    unittest.main()
