"""Execution of approved flows: ordering, pass-2 CAS and recoverable failures."""

import typing
from unittest import mock

from uds.core.types.mcp import FlowActionStatus, FlowStatus
from uds.mutability import StalePolicy
from uds.mutability.base import JsonObject
from uds.mutability.executor import execute_flow
from uds.models import ActionFlow, FlowAction

from tests.mcp.mutability._helpers import FlowTestCase

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


class _FakeActionType:
    """Registry double (patched as a class): records executions, forces CAS.

    The registry stores classes and call sites instantiate per use, so
    tests patch ``uds.mutability.registry.get`` with a class built by
    :func:`_fake_type`; the fresh instance appends to the class-level
    ``executed`` log, which the test can assert on.
    """

    stale_policy = StalePolicy.DENY
    drifted_fields: typing.ClassVar[dict[str, dict[str, typing.Any]]] = {}
    fail_targets: frozenset[str] = frozenset()
    vanish_targets: frozenset[str] = frozenset()
    executed: typing.ClassVar[list[str]] = []

    def resolve_target(self, target_uuid: str) -> object:
        if target_uuid in self.vanish_targets:
            raise LookupError(f"target {target_uuid} vanished")
        return object()

    def field_drift(self, action: FlowAction, *, ref_values: dict[str, typing.Any]) -> JsonObject:
        """Pass-2 check result: only the touched fields matter."""
        return self.drifted_fields

    async def execute(self, action: FlowAction, request: typing.Any) -> str:
        if action.target_uuid in self.fail_targets:
            raise RuntimeError(f"cannot update {action.target_uuid}")
        self.executed.append(action.target_uuid)
        return f"updated {action.target_uuid}"


def _fake_type(**attrs: typing.Any) -> type[_FakeActionType]:
    """A fresh fake class (own execution log) with attribute overrides."""
    return type("FakeActionType", (_FakeActionType,), {"executed": [], **attrs})


class ExecuteFlowTest(FlowTestCase):
    """executor.execute_flow drives the approved actions through the store."""

    def _flow_with_actions(
        self, targets: list[str], *, approved_etag: str = "base-etag"
    ) -> tuple[ActionFlow, list[FlowAction]]:
        flow = self._flow(name="exec")
        actions = [
            self.store.add_action(
                flow,
                action_type="provider.update",
                target_uuid=target,
                values={"name": target},
                base_values={"name": "old"},
                base_etag="base-etag",
            )
            for target in targets
        ]
        # Simulate the completed per-action approval cycle
        for action in flow.actions.all():
            action.status = FlowActionStatus.APPROVED
            action.approved_etag = approved_etag
            action.save(update_fields=["status"])
        self.store.approve_flow(flow, admin=self.other)
        return flow, actions

    def test_executes_actions_in_order(self) -> None:
        flow, actions = self._flow_with_actions(["p1", "p2"])

        fake = _fake_type()
        with mock.patch("uds.mutability.registry.get", return_value=fake):
            summary = execute_flow(flow, request=object())

        self.assertEqual(fake.executed, ["p1", "p2"])
        self.assertEqual(summary["status"], FlowStatus.EXECUTED)
        self.assertEqual([r["ok"] for r in summary["results"]], [True, True])
        actions[0].refresh_from_db()
        actions[1].refresh_from_db()
        self.assertEqual(actions[0].status, FlowActionStatus.EXECUTED)
        self.assertEqual(actions[1].status, FlowActionStatus.EXECUTED)
        flow.refresh_from_db()
        self.assertEqual(flow.status, FlowStatus.EXECUTED)

    def test_unrelated_drift_does_not_block(self) -> None:
        """Pass 2 only checks the touched fields, not the whole item."""
        flow, _actions = self._flow_with_actions(["p1"], approved_etag="approved-etag")

        # The whole item changed (fingerprint differs) but none of the
        # fields this action touches did
        fake = _fake_type(drifted_fields={})
        with mock.patch("uds.mutability.registry.get", return_value=fake):
            summary = execute_flow(flow, request=object())

        self.assertEqual(fake.executed, ["p1"])
        self.assertEqual(summary["status"], FlowStatus.EXECUTED)

    def test_failure_returns_flow_to_locked_and_keeps_rest_approved(self) -> None:
        flow, actions = self._flow_with_actions(["p1", "p2"])

        fake = _fake_type(fail_targets=frozenset({"p1"}))
        with mock.patch("uds.mutability.registry.get", return_value=fake):
            summary = execute_flow(flow, request=object())

        self.assertEqual(fake.executed, [])  # first failed, nothing ran
        self.assertEqual(summary["status"], FlowStatus.LOCKED)
        actions[0].refresh_from_db()
        actions[1].refresh_from_db()
        self.assertEqual(actions[0].status, FlowActionStatus.FAILED)
        self.assertIn("cannot update p1", str(actions[0].properties.get("result")))
        # Recoverable: the remaining approved action keeps its approval
        self.assertEqual(actions[1].status, FlowActionStatus.APPROVED)
        flow.refresh_from_db()
        self.assertEqual(flow.status, FlowStatus.LOCKED)
        # Where it stopped and why
        self.assertIn("stopped at action 1", str(flow.properties.get("run_note")))
        self.assertIn("cannot update p1", str(flow.properties.get("run_note")))

    def test_field_drift_after_approval_revokes_and_locks_flow(self) -> None:
        flow, actions = self._flow_with_actions(["p1", "p2"], approved_etag="approved-etag")

        # A touched field no longer matches the frozen approval
        fake = _fake_type(drifted_fields={"name": {"approved": "old", "live": "changed by someone else"}})
        with mock.patch("uds.mutability.registry.get", return_value=fake):
            summary = execute_flow(flow, request=object())

        self.assertEqual(fake.executed, [])  # revoked before running
        self.assertEqual(summary["status"], FlowStatus.LOCKED)
        self.assertEqual(summary["results"][0].get("revoked"), True)
        actions[0].refresh_from_db()
        actions[1].refresh_from_db()
        # Tried and refused: revoked, not failed
        self.assertEqual(actions[0].status, FlowActionStatus.REVOKED)
        result = str(actions[0].properties.get("result"))
        self.assertIn("field 'name' changed after approval", result)
        self.assertIn("'old'", result)
        self.assertIn("'changed by someone else'", result)
        # Recoverable: the remaining approved action keeps its approval
        self.assertEqual(actions[1].status, FlowActionStatus.APPROVED)
        flow.refresh_from_db()
        self.assertEqual(flow.status, FlowStatus.LOCKED)

    def test_vanished_target_revokes_instead_of_executing(self) -> None:
        flow, actions = self._flow_with_actions(["p1"])

        fake = _fake_type(vanish_targets=frozenset({"p1"}))
        with mock.patch("uds.mutability.registry.get", return_value=fake):
            summary = execute_flow(flow, request=object())

        self.assertEqual(summary["status"], FlowStatus.LOCKED)
        actions[0].refresh_from_db()
        self.assertEqual(actions[0].status, FlowActionStatus.REVOKED)
        self.assertIn("no longer exists", str(actions[0].properties.get("result")))

    def test_retry_after_failure_runs_only_the_rest(self) -> None:
        """Relaunch of a partially-run flow: executed actions never repeat."""
        flow, actions = self._flow_with_actions(["p1", "p2"])

        # First run: p1 fails, the flow is locked with p2 still approved
        failed = _fake_type(fail_targets=frozenset({"p1"}))
        with mock.patch("uds.mutability.registry.get", return_value=failed):
            execute_flow(flow, request=object())
        flow.refresh_from_db()
        self.assertEqual(flow.status, FlowStatus.LOCKED)

        # Recovery: the failed action is re-approved (as the admin does)
        actions[0].status = FlowActionStatus.APPROVED
        actions[0].save(update_fields=["status"])
        self.store.approve_flow(flow, admin=self.other)

        retried = _fake_type()
        with mock.patch("uds.mutability.registry.get", return_value=retried):
            summary = execute_flow(flow, request=object())

        # p1 (failed -> re-approved) and p2 (kept approved) both run now;
        # an EXECUTED action would not even reach the retry (not APPROVED)
        self.assertEqual(retried.executed, ["p1", "p2"])
        self.assertEqual(summary["status"], FlowStatus.EXECUTED)

    def test_executed_actions_are_not_rerun_on_relaunch(self) -> None:
        flow, actions = self._flow_with_actions(["p1", "p2"])

        # First run: p1 executes, p2 fails -> flow locked, p2 approved
        first_run = _fake_type(fail_targets=frozenset({"p2"}))
        with mock.patch("uds.mutability.registry.get", return_value=first_run):
            execute_flow(flow, request=object())
        actions[0].refresh_from_db()
        self.assertEqual(actions[0].status, FlowActionStatus.EXECUTED)

        # Recovery and relaunch
        actions[1].status = FlowActionStatus.APPROVED
        actions[1].save(update_fields=["status"])
        self.store.approve_flow(flow, admin=self.other)
        retried = _fake_type()
        with mock.patch("uds.mutability.registry.get", return_value=retried):
            summary = execute_flow(flow, request=object())

        # p1 stays executed and is NOT repeated; only p2 runs
        self.assertEqual(retried.executed, ["p2"])
        actions[0].refresh_from_db()
        self.assertEqual(actions[0].status, FlowActionStatus.EXECUTED)
        self.assertEqual(summary["status"], FlowStatus.EXECUTED)

    def test_force_policy_skips_execution_cas(self) -> None:
        flow, _actions = self._flow_with_actions(["p1"], approved_etag="approved-etag")

        fake = _fake_type(stale_policy=StalePolicy.FORCE, drifted_fields={"name": {}})
        with mock.patch("uds.mutability.registry.get", return_value=fake):
            summary = execute_flow(flow, request=object())

        self.assertEqual(summary["status"], FlowStatus.EXECUTED)
        self.assertEqual(fake.executed, ["p1"])

    def test_refuses_non_approved_flow(self) -> None:
        flow = self._flow(name="pending")
        self._action(flow)
        with self.assertRaises(ValueError):
            execute_flow(flow, request=object())
