"""Flow lifecycle (strict state machine) and model-backed store behavior."""

import datetime
import typing
from unittest import mock

from uds.core.consts import mcp as consts_mcp
from uds.core.types.mcp import FlowActionStatus, FlowStatus
from uds.mutability import (
    FlowStore,
    InvalidTransition,
    MutabilityError,
    NotActionOwner,
)
from uds.models import ActionFlow, FlowAction

from tests.fixtures.authenticators import create_db_authenticator, create_db_users
from tests.utils.test import UDSTestCase

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownLambdaType=false


class _FlowTestCase(UDSTestCase):
    """Common fixtures: an owner user and a fresh store."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.authenticator = create_db_authenticator()
        users = create_db_users(self.authenticator, number_of_users=2)
        self.owner = users[0]
        self.other = users[1]
        self.store = FlowStore()

    def _flow(self, **kwargs: typing.Any) -> ActionFlow:
        return self.store.create_flow(
            owner=kwargs.get("owner", self.owner),
            agent=kwargs.get("agent", "test-agent/1.0"),
            name=kwargs.get("name", "test flow"),
            justification=kwargs.get("justification", "because"),
        )

    def _action(self, flow: ActionFlow, **kwargs: typing.Any) -> FlowAction:
        return self.store.add_action(
            flow,
            action_type=kwargs.get("action_type", "provider.update"),
            target_uuid=kwargs.get("target_uuid", "target-1"),
            values=kwargs.get("values", {"name": "new name"}),
            base_values=kwargs.get("base_values", {"name": "old name"}),
            base_etag=kwargs.get("base_etag", "abc123"),
        )


class FlowLifecycleTest(_FlowTestCase):
    """Strict state machine: valid transitions and rejected ones."""

    def test_created_is_pending_with_due_date(self) -> None:
        flow = self._flow()
        self.assertEqual(flow.status, FlowStatus.PENDING)
        self.assertIsNotNone(flow.due_date)
        assert flow.due_date is not None
        remaining = flow.due_date - datetime.datetime.now(datetime.UTC)
        self.assertAlmostEqual(remaining.total_seconds(), consts_mcp.FLOW_TTL_DAYS * 86400, delta=60)
        self.assertEqual(flow.properties.get("agent"), "test-agent/1.0")

    def test_add_action_orders_sequentially(self) -> None:
        flow = self._flow()
        first = self._action(flow, values={"n": 1})
        second = self._action(flow, values={"n": 2})
        self.assertEqual((first.order, second.order), (1, 2))
        self.assertEqual(first.status, FlowActionStatus.PENDING)

    def test_add_action_respects_size_cap(self) -> None:
        flow = self._flow()
        with mock.patch("uds.core.consts.mcp.MAX_ACTIONS_PER_FLOW", 2):
            self._action(flow, values={"n": 1})
            self._action(flow, values={"n": 2})
            with self.assertRaises(MutabilityError):
                self._action(flow, values={"n": 3})

    def test_add_action_on_decided_flow_raises(self) -> None:
        flow = self._flow()
        self.store.cancel_flow(flow, actor_uuid=str(self.owner.uuid))
        with self.assertRaises(InvalidTransition):
            self._action(flow)

    def test_cancel_by_owner_skips_pending_actions(self) -> None:
        flow = self._flow()
        action = self._action(flow)
        self.store.cancel_flow(flow, actor_uuid=str(self.owner.uuid))
        self.assertEqual(flow.status, FlowStatus.CANCELLED)
        action.refresh_from_db()
        self.assertEqual(action.status, FlowActionStatus.SKIPPED)
        self.assertEqual(flow.properties.get("decided_by"), str(self.owner.uuid))

    def test_cancel_by_other_user_raises(self) -> None:
        flow = self._flow()
        with self.assertRaises(NotActionOwner):
            self.store.cancel_flow(flow, actor_uuid=str(self.other.uuid))

    def test_reject_records_admin_and_reason(self) -> None:
        flow = self._flow()
        action = self._action(flow)
        self.store.reject_flow(flow, admin="admin-1", reason="not now")
        self.assertEqual(flow.status, FlowStatus.REJECTED)
        self.assertEqual(flow.properties.get("decided_by"), "admin-1")
        self.assertEqual(flow.properties.get("decided_note"), "not now")
        action.refresh_from_db()
        self.assertEqual(action.status, FlowActionStatus.SKIPPED)

    def test_approve_records_admin_and_approves_actions(self) -> None:
        flow = self._flow()
        action = self._action(flow)
        self.store.approve_flow(flow, admin=self.other)
        self.assertEqual(flow.status, FlowStatus.APPROVED)
        self.assertEqual(flow.approved_by, self.other)
        self.assertIsNotNone(flow.approved_at)
        action.refresh_from_db()
        self.assertEqual(action.status, FlowActionStatus.APPROVED)

    def test_decided_is_terminal(self) -> None:
        decisions = (
            lambda flow: self.store.approve_flow(flow, admin=self.other),
            lambda flow: self.store.reject_flow(flow, admin="admin-1"),
            lambda flow: self.store.cancel_flow(flow, actor_uuid=str(self.owner.uuid)),
            lambda flow: self.store._decide_flow(  # pyright: ignore[reportPrivateUsage]
                flow, FlowStatus.EXPIRED, decided_by=None, note="Expired"
            ),
        )
        for decide in decisions:
            flow = self._flow()
            decide(flow)
            with self.assertRaises(InvalidTransition):
                self.store.approve_flow(flow, admin=self.other)
            with self.assertRaises(InvalidTransition):
                self.store.reject_flow(flow, admin="admin-1")
            with self.assertRaises(InvalidTransition):
                self.store.cancel_flow(flow, actor_uuid=str(self.owner.uuid))
            with self.assertRaises(InvalidTransition):
                self.store._decide_flow(  # pyright: ignore[reportPrivateUsage]
                    flow, FlowStatus.EXPIRED, decided_by=None, note="Expired"
                )

    def test_execution_flow_states(self) -> None:
        flow = self._flow()
        first = self._action(flow, values={"n": 1})
        second = self._action(flow, values={"n": 2})
        self.store.approve_flow(flow, admin=self.other)
        first.refresh_from_db()
        second.refresh_from_db()

        self.store.mark_action_executing(first)
        self.assertEqual(first.status, FlowActionStatus.EXECUTING)
        self.store.mark_action_result(first, result="provider renamed")
        flow.refresh_from_db()
        self.assertEqual(flow.status, FlowStatus.EXECUTING)  # second action still pending

        self.store.mark_action_executing(second)
        self.store.mark_action_result(second, result="provider commented")
        flow.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(flow.status, FlowStatus.EXECUTED)
        self.assertEqual(second.status, FlowActionStatus.EXECUTED)
        self.assertEqual(second.properties.get("result"), "provider commented")

    def test_stop_on_failure_skips_remaining(self) -> None:
        flow = self._flow()
        first = self._action(flow, values={"n": 1})
        second = self._action(flow, values={"n": 2})
        self.store.approve_flow(flow, admin=self.other)
        first.refresh_from_db()

        self.store.mark_action_executing(first)
        self.store.mark_action_result(first, result="target vanished", failed=True)
        flow.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(flow.status, FlowStatus.FAILED)
        self.assertEqual(first.status, FlowActionStatus.FAILED)
        self.assertEqual(second.status, FlowActionStatus.SKIPPED)

    def test_execution_requires_approved_or_executing(self) -> None:
        flow = self._flow()
        action = self._action(flow)
        with self.assertRaises(InvalidTransition):
            self.store.mark_action_executing(action)  # flow still pending
        self.store.approve_flow(flow, admin=self.other)
        action.refresh_from_db()
        self.store.mark_action_executing(action)
        self.store.mark_action_result(action, result="done")
        with self.assertRaises(InvalidTransition):
            self.store.mark_action_result(action, result="again")


class FlowStoreTest(_FlowTestCase):
    """Model-backed persistence, listing filters and lazy expiry."""

    def test_get_flow_and_action(self) -> None:
        flow = self._flow()
        action = self._action(flow)
        loaded_flow = self.store.get_flow(flow.uuid)
        assert loaded_flow is not None
        self.assertEqual(loaded_flow.uuid, flow.uuid)
        loaded_action = self.store.get_action(action.uuid)
        assert loaded_action is not None
        self.assertEqual(loaded_action.values, {"name": "new name"})
        self.assertEqual(loaded_action.flow.uuid, flow.uuid)

    def test_get_missing_returns_none(self) -> None:
        self.assertIsNone(self.store.get_flow("nonexistent-uuid"))
        self.assertIsNone(self.store.get_action("nonexistent-uuid"))

    def test_list_flows_filters_by_status_and_owner(self) -> None:
        self._flow(name="pending")
        approved = self._flow(name="approved")
        self.store.approve_flow(approved, admin=self.other)
        self._flow(owner=self.other, name="foreign")

        mine = self.store.list_flows(owner_uuid=str(self.owner.uuid))
        self.assertEqual({f.name for f in mine}, {"pending", "approved"})

        pendings = self.store.list_flows(status=FlowStatus.PENDING, owner_uuid=str(self.owner.uuid))
        self.assertEqual([f.name for f in pendings], ["pending"])

    def test_list_actions_orders_by_flow_and_sequence(self) -> None:
        flow_a = self._flow(name="a")
        first = self._action(flow_a, values={"n": 1})
        second = self._action(flow_a, values={"n": 2})
        flow_b = self._flow(name="b")
        third = self._action(flow_b, values={"n": 3})

        listed = self.store.list_actions(owner_uuid=str(self.owner.uuid))
        self.assertEqual([a.uuid for a in listed], [first.uuid, second.uuid, third.uuid])

        only_b = self.store.list_actions(owner_uuid=str(self.owner.uuid), action_type="nonexistent.type")
        self.assertEqual(only_b, [])

    def test_count_pending_flows(self) -> None:
        self.assertGreater(consts_mcp.MAX_FLOWS_PER_USER, 0)
        for i in range(3):
            flow = self._flow(name=f"flow-{i}")
            self._action(flow)
        decided = self._flow(name="decided")
        self.store.cancel_flow(decided, actor_uuid=str(self.owner.uuid))

        self.assertEqual(self.store.count_pending_flows(owner_uuid=str(self.owner.uuid)), 3)
        self.assertEqual(self.store.count_pending_flows(owner_uuid=str(self.other.uuid)), 0)

    def test_create_flow_enforces_cap(self) -> None:
        with mock.patch("uds.core.consts.mcp.MAX_FLOWS_PER_USER", 2):
            self._flow(name="one")
            self._flow(name="two")
            with self.assertRaises(MutabilityError):
                self._flow(name="three")

    def test_lazy_expiry(self) -> None:
        flow = self._flow()
        action = self._action(flow)
        # Force the due date into the past
        ActionFlow.objects.filter(uuid=flow.uuid).update(
            due_date=datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1)
        )

        # Any access expires it on the fly
        loaded = self.store.get_flow(flow.uuid)
        assert loaded is not None
        self.assertEqual(loaded.status, FlowStatus.EXPIRED)
        action.refresh_from_db()
        self.assertEqual(action.status, FlowActionStatus.SKIPPED)

        # ... and it is no longer listed as pending
        self.assertEqual(self.store.list_flows(status=FlowStatus.PENDING), [])

    def test_fresh_pending_is_not_expired(self) -> None:
        flow = self._flow()
        loaded = self.store.get_flow(flow.uuid)
        assert loaded is not None
        self.assertEqual(loaded.status, FlowStatus.PENDING)
