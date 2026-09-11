"""Flow lifecycle (strict state machine) and model-backed store behavior."""

import datetime
import typing
from unittest import mock

from uds.core.consts import mcp as consts_mcp
from uds.core.types.mcp import FlowActionStatus, FlowStatus
from uds.core.util.config import GlobalConfig
from uds.mutability import (
    InvalidTransition,
    MutabilityError,
    NotActionOwner,
    StaleProposal,
    StalePolicy,
)
from uds.models import ActionFlow, FlowAction
from uds.mutability.types_providers import ProviderUpdate

from tests.fixtures.services import create_db_provider
from tests.mcp.mutability._helpers import FlowTestCase

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownLambdaType=false


def _config_limit(name: str, value: int) -> typing.Any:
    """Patch a GlobalConfig MCP limit (store reads them at call time)."""
    return mock.patch.object(GlobalConfig, name, mock.Mock(as_int=mock.Mock(return_value=value)))


def _approve_all(flow: ActionFlow) -> None:
    """Simulate a completed per-action approval cycle (no registry)."""
    for action in flow.actions.all():
        action.status = FlowActionStatus.APPROVED
        action.approved_etag = "frozen-etag"
        action.save(update_fields=["status"])


class FlowLifecycleTest(FlowTestCase):
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
        with _config_limit("MCP_MAX_ACTIONS_PER_FLOW", 2):
            self._action(flow, values={"n": 1})
            self._action(flow, values={"n": 2})
            with self.assertRaises(MutabilityError):
                self._action(flow, values={"n": 3})

    def test_add_action_on_decided_flow_raises(self) -> None:
        flow = self._flow()
        self.store.cancel_flow(flow, actor_uuid=self.owner.uuid)
        with self.assertRaises(InvalidTransition):
            self._action(flow)

    def test_cancel_by_owner_skips_pending_actions(self) -> None:
        flow = self._flow()
        action = self._action(flow)
        self.store.cancel_flow(flow, actor_uuid=self.owner.uuid)
        self.assertEqual(flow.status, FlowStatus.CANCELLED)
        action.refresh_from_db()
        self.assertEqual(action.status, FlowActionStatus.SKIPPED)
        self.assertEqual(flow.properties.get("decided_by"), self.owner.uuid)

    def test_cancel_by_other_user_raises(self) -> None:
        flow = self._flow()
        with self.assertRaises(NotActionOwner):
            self.store.cancel_flow(flow, actor_uuid=self.other.uuid)

    def test_reject_records_admin_and_reason(self) -> None:
        flow = self._flow()
        action = self._action(flow)
        self.store.reject_flow(flow, admin="admin-1", reason="not now")
        self.assertEqual(flow.status, FlowStatus.REJECTED)
        self.assertEqual(flow.properties.get("decided_by"), "admin-1")
        self.assertEqual(flow.properties.get("decided_note"), "not now")
        action.refresh_from_db()
        self.assertEqual(action.status, FlowActionStatus.SKIPPED)

    def test_approve_requires_all_actions_ready(self) -> None:
        flow = self._flow()
        self._action(flow)  # pending: the approval cycle is not done
        with self.assertRaises(InvalidTransition):
            self.store.approve_flow(flow, admin=self.other)

    def test_approve_records_admin(self) -> None:
        flow = self._flow()
        action = self._action(flow)
        _approve_all(flow)
        self.store.approve_flow(flow, admin=self.other)
        self.assertEqual(flow.status, FlowStatus.APPROVED)
        self.assertEqual(flow.approved_by, self.other)
        self.assertIsNotNone(flow.approved_at)
        action.refresh_from_db()
        self.assertEqual(action.status, FlowActionStatus.APPROVED)

    def test_approve_flow_from_locked(self) -> None:
        flow = self._flow()
        self._action(flow)
        _approve_all(flow)
        self.store.lock_flow(flow, admin=self.other)
        self.assertEqual(flow.status, FlowStatus.LOCKED)
        self.store.approve_flow(flow, admin=self.other)
        flow.refresh_from_db()
        self.assertEqual(flow.status, FlowStatus.APPROVED)

    def test_decided_is_terminal(self) -> None:
        def ready(flow: ActionFlow) -> ActionFlow:
            _approve_all(flow)
            return flow

        decisions = (
            lambda flow: self.store.approve_flow(ready(flow), admin=self.other),
            lambda flow: self.store.reject_flow(flow, admin="admin-1"),
            lambda flow: self.store.cancel_flow(flow, actor_uuid=self.owner.uuid),
            lambda flow: self.store._decide_flow(  # pyright: ignore[reportPrivateUsage]
                flow, FlowStatus.EXPIRED, allowed=(FlowStatus.PENDING,), decided_by=None, note="Expired"
            ),
        )
        for decide in decisions:
            flow = self._flow()
            self._action(flow)  # an undecided action; decisions skip it
            decide(flow)
            with self.assertRaises(InvalidTransition):
                self.store.approve_flow(flow, admin=self.other)
            with self.assertRaises(InvalidTransition):
                self.store.reject_flow(flow, admin="admin-1")
            with self.assertRaises(InvalidTransition):
                self.store.cancel_flow(flow, actor_uuid=self.owner.uuid)
            with self.assertRaises(InvalidTransition):
                self.store._decide_flow(  # pyright: ignore[reportPrivateUsage]
                    flow, FlowStatus.EXPIRED, allowed=(FlowStatus.PENDING,), decided_by=None, note="Expired"
                )

    def test_execution_flow_states(self) -> None:
        flow = self._flow()
        first = self._action(flow, values={"n": 1})
        second = self._action(flow, values={"n": 2})
        _approve_all(flow)
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

    def test_failure_returns_flow_to_locked_and_keeps_rest_approved(self) -> None:
        flow = self._flow()
        first = self._action(flow, values={"n": 1})
        second = self._action(flow, values={"n": 2})
        _approve_all(flow)
        self.store.approve_flow(flow, admin=self.other)
        first.refresh_from_db()

        self.store.mark_action_executing(first)
        self.store.mark_action_result(first, result="target gone", failed=True)
        flow.refresh_from_db()
        second.refresh_from_db()
        # Recoverable: back to the administrator's hands, the rest keeps
        # its approval for the retry
        self.assertEqual(flow.status, FlowStatus.LOCKED)
        self.assertEqual(first.status, FlowActionStatus.FAILED)
        self.assertEqual(second.status, FlowActionStatus.APPROVED)

    def test_relaunch_after_partial_execution_skips_executed(self) -> None:
        """A partially-run flow relaunches; executed actions never repeat."""
        flow = self._flow()
        first = self._action(flow, values={"n": 1})
        second = self._action(flow, values={"n": 2})
        _approve_all(flow)
        self.store.approve_flow(flow, admin=self.other)
        first.refresh_from_db()
        second.refresh_from_db()

        # First runs fine, second fails: the flow is locked again
        self.store.mark_action_executing(first)
        self.store.mark_action_result(first, result="done")
        self.store.mark_action_executing(second)
        self.store.mark_action_result(second, result="boom", failed=True)
        flow.refresh_from_db()
        self.assertEqual(flow.status, FlowStatus.LOCKED)

        # The administrator re-approves the failed one (simulated: the
        # targets here are fake); the executed one is NOT re-approvable
        # (redoing is a new proposal, not a relaunch)
        second.status = FlowActionStatus.APPROVED
        second.save(update_fields=["status"])
        with self.assertRaises(InvalidTransition):
            self.store.approve_action(first, admin=self.other)

        # approve_flow accepts executed actions as ready: the relaunch
        # composes (the CAS strictness of pass 1 is covered with real
        # targets in FlowApproveCasTest)
        self.store.approve_flow(flow, admin=self.other)
        flow.refresh_from_db()
        self.assertEqual(flow.status, FlowStatus.APPROVED)
        first.refresh_from_db()
        self.assertEqual(first.status, FlowActionStatus.EXECUTED)

    def test_execution_requires_approved_or_executing(self) -> None:
        flow = self._flow()
        action = self._action(flow)
        with self.assertRaises(InvalidTransition):
            self.store.mark_action_executing(action)  # flow still pending
        _approve_all(flow)
        self.store.approve_flow(flow, admin=self.other)
        action.refresh_from_db()
        self.store.mark_action_executing(action)
        self.store.mark_action_result(action, result="done")
        with self.assertRaises(InvalidTransition):
            self.store.mark_action_result(action, result="again")


class FlowStoreTest(FlowTestCase):
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
        self._action(approved)
        _approve_all(approved)
        self.store.approve_flow(approved, admin=self.other)
        self._flow(owner=self.other, name="foreign")

        mine = self.store.list_flows(owner_uuid=self.owner.uuid)
        self.assertEqual({f.name for f in mine}, {"pending", "approved"})

        pendings = self.store.list_flows(status=FlowStatus.PENDING, owner_uuid=self.owner.uuid)
        self.assertEqual([f.name for f in pendings], ["pending"])

    def test_list_actions_orders_by_flow_and_sequence(self) -> None:
        flow_a = self._flow(name="a")
        first = self._action(flow_a, values={"n": 1})
        second = self._action(flow_a, values={"n": 2})
        flow_b = self._flow(name="b")
        third = self._action(flow_b, values={"n": 3})

        listed = self.store.list_actions(owner_uuid=self.owner.uuid)
        self.assertEqual([a.uuid for a in listed], [first.uuid, second.uuid, third.uuid])

        only_b = self.store.list_actions(owner_uuid=self.owner.uuid, action_type="nonexistent.type")
        self.assertEqual(only_b, [])

    def test_count_pending_flows(self) -> None:
        self.assertGreater(consts_mcp.MAX_FLOWS_PER_USER, 0)
        for i in range(3):
            flow = self._flow(name=f"flow-{i}")
            self._action(flow)
        decided = self._flow(name="decided")
        self.store.cancel_flow(decided, actor_uuid=self.owner.uuid)

        self.assertEqual(self.store.count_pending_flows(owner_uuid=self.owner.uuid), 3)
        self.assertEqual(self.store.count_pending_flows(owner_uuid=self.other.uuid), 0)

    def test_create_flow_enforces_cap(self) -> None:
        with _config_limit("MCP_MAX_FLOWS_PER_USER", 2):
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


class FlowApproveCasTest(FlowTestCase):
    """Per-action approve/skip lifecycle, the locked flow and the two-pass CAS."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.provider = create_db_provider()
        self.action_type = ProviderUpdate()
        self.flow = self._flow()

    def _add_proposal(self) -> FlowAction:
        values = {"name": "renamed by agent"}
        base_values, base_etag = self.action_type.snapshot_and_fingerprint(self.provider, values)
        return self.store.add_action(
            self.flow,
            action_type="provider.update",
            target_uuid=self.provider.uuid,
            values=values,
            base_values=base_values,
            base_etag=base_etag,
        )

    # -------------------------------------------------------------- approve

    def test_approve_freezes_live_state_and_snapshots_it(self) -> None:
        action = self._add_proposal()
        approved = self.store.approve_action(action, admin=self.other)
        self.assertEqual(approved.status, FlowActionStatus.APPROVED)
        # Proposal base stays immutable
        self.assertEqual(approved.base_etag, action.base_etag)
        # Untouched target: frozen fingerprint matches the proposal base
        self.assertEqual(approved.approved_etag, action.base_etag)
        self.assertEqual(approved.approved_values, {"name": self.provider.name})
        snap = approved.snap_info
        self.assertTrue(snap.get("target_name"))
        self.assertEqual(snap.get("current_values"), {"name": self.provider.name})
        self.assertEqual(snap.get("approved_by"), self.other.name)
        self.assertIn("approved_at", snap)
        self.assertTrue(snap.get("fields"))

    def test_approve_acquires_pending_flow(self) -> None:
        action = self._add_proposal()
        self.assertEqual(self.flow.status, FlowStatus.PENDING)
        self.store.approve_action(action, admin=self.other)
        self.flow.refresh_from_db()
        # First admin touch acquires the flow: invisible to the agent
        self.assertEqual(self.flow.status, FlowStatus.LOCKED)
        self.assertEqual(self.flow.properties.get("locked_by"), self.other.name)

    def test_approve_freezes_the_drifted_state_it_saw(self) -> None:
        action = self._add_proposal()
        self.provider.name = "changed by a human"
        self.provider.save()
        self.store.approve_action(action, admin=self.other)
        action.refresh_from_db()
        # The frozen reference is the live (drifted) state, not the base
        self.assertNotEqual(action.approved_etag, action.base_etag)
        self.assertEqual(action.approved_values, {"name": "changed by a human"})

    def test_reapprove_refreshes_the_freeze(self) -> None:
        action = self._add_proposal()
        self.store.approve_action(action, admin=self.other)
        self.provider.name = "changed later"
        self.provider.save()
        self.store.approve_action(action, admin=self.other)
        action.refresh_from_db()
        self.assertEqual(action.approved_values, {"name": "changed later"})

    def test_approve_of_decided_flow_raises(self) -> None:
        action = self._add_proposal()
        self.store.cancel_flow(self.flow, actor_uuid=self.owner.uuid)
        with self.assertRaises(InvalidTransition):
            self.store.approve_action(action, admin=self.other)

    def test_approve_of_executing_action_raises(self) -> None:
        action = self._add_proposal()
        self.store.approve_action(action, admin=self.other)
        self.store.approve_flow(self.flow, admin=self.other)
        self.store.mark_action_executing(action)
        with self.assertRaises(InvalidTransition):
            self.store.approve_action(action, admin=self.other)

    def test_approve_of_vanished_target_raises(self) -> None:
        action = self._add_proposal()
        self.provider.delete()
        with self.assertRaises(StaleProposal):
            self.store.approve_action(action, admin=self.other)

    def test_revoked_action_is_recovered_by_approve(self) -> None:
        action = self._add_proposal()
        self.store.approve_action(action, admin=self.other)
        self.provider.name = "changed by a human"
        self.provider.save()
        self.store.verify_flow(self.flow)  # pass 1 revokes it
        action.refresh_from_db()
        self.assertEqual(action.status, FlowActionStatus.REVOKED)
        # The administrator re-approves: fresh freeze, ready to launch
        recovered = self.store.approve_action(action, admin=self.other)
        self.assertEqual(recovered.status, FlowActionStatus.APPROVED)
        self.assertEqual(self.store.verify_flow(self.flow), [])

    # ----------------------------------------------------------------- skip

    def test_skip_marks_and_acquires_flow(self) -> None:
        action = self._add_proposal()
        skipped = self.store.skip_action(action, admin=self.other)
        self.assertEqual(skipped.status, FlowActionStatus.SKIPPED)
        self.flow.refresh_from_db()
        self.assertEqual(self.flow.status, FlowStatus.LOCKED)

    def test_skip_clears_frozen_approval(self) -> None:
        action = self._add_proposal()
        self.store.approve_action(action, admin=self.other)
        self.store.skip_action(action, admin=self.other)
        action.refresh_from_db()
        self.assertEqual(action.approved_etag, "")
        self.assertEqual(action.approved_values, {})

    def test_skip_of_executed_action_raises(self) -> None:
        action = self._add_proposal()
        self.store.approve_action(action, admin=self.other)
        self.store.approve_flow(self.flow, admin=self.other)
        self.store.mark_action_executing(action)
        self.store.mark_action_result(action, result="done")
        with self.assertRaises(InvalidTransition):
            self.store.skip_action(action, admin=self.other)

    # ----------------------------------------------------------------- lock

    def test_lock_flow(self) -> None:
        self._add_proposal()
        self.store.lock_flow(self.flow, admin=self.other)
        self.flow.refresh_from_db()
        self.assertEqual(self.flow.status, FlowStatus.LOCKED)
        self.assertEqual(self.flow.properties.get("locked_by"), self.other.name)

    def test_lock_is_idempotent(self) -> None:
        self._add_proposal()
        self.store.lock_flow(self.flow, admin=self.other)
        self.store.lock_flow(self.flow, admin=self.other)
        self.flow.refresh_from_db()
        self.assertEqual(self.flow.status, FlowStatus.LOCKED)

    def test_lock_refuses_flows_past_locked(self) -> None:
        action = self._add_proposal()
        self.store.approve_action(action, admin=self.other)
        self.store.approve_flow(self.flow, admin=self.other)
        with self.assertRaises(InvalidTransition):
            self.store.lock_flow(self.flow, admin=self.other)

    def test_owner_cannot_edit_or_cancel_locked_flow(self) -> None:
        action = self._add_proposal()
        self.store.approve_action(action, admin=self.other)  # acquires the flow
        with self.assertRaises(InvalidTransition):
            self.store.rebase_action(action, values={"name": "x"}, base_values={"name": "y"}, base_etag="e")
        with self.assertRaises(InvalidTransition):
            self.store.cancel_flow(self.flow, actor_uuid=self.owner.uuid)

    # ------------------------------------------------------------ compliance

    def test_compliance_ok_when_untouched(self) -> None:
        action = self._add_proposal()
        self.assertEqual(self.store.compliance(action), "ok")
        self.store.approve_action(action, admin=self.other)
        self.assertEqual(self.store.compliance(action), "ok")

    def test_compliance_conflict_when_touched_field_drifts(self) -> None:
        action = self._add_proposal()
        self.provider.name = "changed by a human"
        self.provider.save()
        # Pending: compared against the proposal base
        self.assertEqual(self.store.compliance(action), "conflict")
        # Approving freezes the drifted state: it becomes the reference
        self.store.approve_action(action, admin=self.other)
        self.assertEqual(self.store.compliance(action), "ok")

    def test_compliance_outdated_when_untouched_field_drifts(self) -> None:
        action = self._add_proposal()
        self.provider.comments = "annotated by a human"
        self.provider.save()
        self.assertEqual(self.store.compliance(action), "outdated")

    def test_compliance_conflict_when_unverifiable(self) -> None:
        action = self._add_proposal()
        self.provider.delete()
        self.assertEqual(self.store.compliance(action), "conflict")

    def test_flow_compliance_aggregates_the_worst(self) -> None:
        first = self._add_proposal()
        second = self.store.add_action(
            self.flow,
            action_type="provider.update",
            target_uuid=self.provider.uuid,
            values={"name": "another rename"},
            base_values={"name": self.provider.name},
            base_etag=self.action_type.fingerprint(self.provider),
        )
        self.assertEqual(self.store.flow_compliance(self.flow), "ok")
        # An unrelated field changed: outdated for both actions
        self.provider.comments = "annotated by a human"
        self.provider.save()
        self.assertEqual(self.store.flow_compliance(self.flow), "outdated")
        # Now a touched field changed: conflict
        self.provider.name = "changed by a human"
        self.provider.save()
        self.assertEqual(self.store.flow_compliance(self.flow), "conflict")
        # Decided actions do not hide the conflicts of the pending ones
        self.store.skip_action(second, admin=self.other)
        self.assertEqual(self.store.flow_compliance(self.flow), "conflict")
        self.store.approve_action(first, admin=self.other)
        self.assertEqual(self.store.flow_compliance(self.flow), "ok")

    # ---------------------------------------------------------------- verify

    def test_verify_flow_ok_when_all_actions_approved_and_fresh(self) -> None:
        action = self._add_proposal()
        self.store.approve_action(action, admin=self.other)
        self.assertEqual(self.store.verify_flow(self.flow), [])
        self.store.approve_flow(self.flow, admin=self.other)
        self.flow.refresh_from_db()
        self.assertEqual(self.flow.status, FlowStatus.APPROVED)
        self.assertEqual(self.flow.approved_by, self.other)

    def test_verify_flow_requires_pending_or_locked_flow(self) -> None:
        action = self._add_proposal()
        self.store.approve_action(action, admin=self.other)  # acquires the flow
        self.store.reject_flow(self.flow, admin="admin-1")
        with self.assertRaises(InvalidTransition):
            self.store.verify_flow(self.flow)

    def test_verify_flow_requires_all_actions_ready(self) -> None:
        self._add_proposal()  # stays pending
        approved = self._add_proposal()
        self.store.approve_action(approved, admin=self.other)
        with self.assertRaises(InvalidTransition):
            self.store.verify_flow(self.flow)

    def test_verify_flow_requires_at_least_one_approved_action(self) -> None:
        action = self._add_proposal()
        self.store.skip_action(action, admin=self.other)
        with self.assertRaises(InvalidTransition):
            self.store.verify_flow(self.flow)

    def test_verify_is_strict_whole_item(self) -> None:
        """Pass 1 revokes on ANY change, even outside the touched fields."""
        action = self._add_proposal()
        self.store.approve_action(action, admin=self.other)
        # Only an unrelated field changed (compliance would be "outdated")
        self.provider.comments = "annotated by a human"
        self.provider.save()
        revoked = self.store.verify_flow(self.flow)
        self.assertEqual([a.uuid for a in revoked], [action.uuid])
        action.refresh_from_db()
        self.assertEqual(action.status, FlowActionStatus.REVOKED)
        # Frozen approval kept as the compliance reference; reason recorded
        self.assertNotEqual(action.approved_etag, "")
        self.assertIn("changed after", str(action.properties.get("result")))
        # The flow keeps its state (locked): recoverable
        self.flow.refresh_from_db()
        self.assertEqual(self.flow.status, FlowStatus.LOCKED)

    def test_verify_tolerates_drift_on_force_types(self) -> None:
        action = self._add_proposal()
        self.store.approve_action(action, admin=self.other)
        self.provider.name = "changed by a human"
        self.provider.save()

        class _ForceProviderUpdate(ProviderUpdate):
            stale_policy = StalePolicy.FORCE

        # verify_flow resolves types through the registry (a class; the
        # store instantiates per use)
        with mock.patch("uds.mutability.registry.get", return_value=_ForceProviderUpdate):
            self.assertEqual(self.store.verify_flow(self.flow), [])

    def test_verify_revokes_unverifiable_actions(self) -> None:
        action = self.store.add_action(
            self.flow,
            action_type="provider.update",
            target_uuid="nonexistent-uuid",
            values={"name": "x"},
            base_values={"name": "y"},
            base_etag="e",
        )
        # Force-approve a vanished target (the approval would refuse it)
        action.status = FlowActionStatus.APPROVED
        action.approved_etag = "frozen"
        action.save(update_fields=["status"])
        revoked = self.store.verify_flow(self.flow)
        self.assertEqual([a.uuid for a in revoked], [action.uuid])
        self.assertIn("no longer exists", str(action.properties.get("result")))

    # ---------------------------------------------------------------- rebase

    def test_rebase_refuses_locked_flow(self) -> None:
        action = self._add_proposal()
        self.store.approve_action(action, admin=self.other)  # flow is locked now
        with self.assertRaises(InvalidTransition):
            self.store.rebase_action(action, values={"name": "x"}, base_values={"name": "y"}, base_etag="e")

    def test_rebase_of_approved_action_refused_even_on_pending_flow(self) -> None:
        action = self._add_proposal()
        self.store.approve_action(action, admin=self.other)
        # Force the flow back to pending (impossible through the API):
        # the approved action itself still refuses a rebase
        self.flow.status = FlowStatus.PENDING
        self.flow.save(update_fields=["status"])
        with self.assertRaises(InvalidTransition):
            self.store.rebase_action(action, values={"name": "x"}, base_values={"name": "y"}, base_etag="e")

    # ---------------------------------------------------------------- revoke

    def test_revoke_only_applies_to_approved_or_executing(self) -> None:
        action = self._add_proposal()
        with self.assertRaises(InvalidTransition):
            self.store.revoke_action(action, reason="nope")
