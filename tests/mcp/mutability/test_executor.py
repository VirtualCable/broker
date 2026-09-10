"""Execution of approved flows: ordering, stop-on-failure and final CAS."""

import typing
from unittest import mock

from uds.core.types.mcp import FlowActionStatus, FlowStatus
from uds.mutability import StalePolicy
from uds.mutability.executor import execute_flow
from uds.models import ActionFlow, FlowAction

from tests.mcp.mutability._helpers import FlowTestCase

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


class _FakeActionType:
    """Registry double: records executions, forces fingerprints/outcomes."""

    stale_policy = StalePolicy.DENY

    def __init__(self, fingerprint: str = "base-etag", fail_targets: set[str] | None = None) -> None:
        self.executed: list[str] = []
        self.fingerprint_value = fingerprint
        self.fail_targets = fail_targets or set()

    def resolve_target(self, target_uuid: str) -> object:
        return object()

    def fingerprint(self, target: object) -> str:
        return self.fingerprint_value

    async def execute(self, action: FlowAction, request: typing.Any) -> str:
        if action.target_uuid in self.fail_targets:
            raise RuntimeError(f"cannot update {action.target_uuid}")
        self.executed.append(action.target_uuid)
        return f"updated {action.target_uuid}"


class ExecuteFlowTest(FlowTestCase):
    """executor.execute_flow drives the approved actions through the store."""

    def _flow_with_actions(self, targets: list[str]) -> tuple[ActionFlow, list[FlowAction]]:
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
        self.store.approve_flow(flow, admin=self.other, approved_etags={a.uuid: "base-etag" for a in actions})
        return flow, actions

    def test_executes_actions_in_order(self) -> None:
        flow, actions = self._flow_with_actions(["p1", "p2"])

        fake = _FakeActionType()
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

    def test_failure_skips_remaining(self) -> None:
        flow, actions = self._flow_with_actions(["p1", "p2"])

        fake = _FakeActionType(fail_targets={"p1"})
        with mock.patch("uds.mutability.registry.get", return_value=fake):
            summary = execute_flow(flow, request=object())

        self.assertEqual(fake.executed, [])  # first failed, nothing ran
        self.assertEqual(summary["status"], FlowStatus.FAILED)
        actions[0].refresh_from_db()
        actions[1].refresh_from_db()
        self.assertEqual(actions[0].status, FlowActionStatus.FAILED)
        self.assertIn("cannot update p1", str(actions[0].properties.get("result")))
        self.assertEqual(actions[1].status, FlowActionStatus.SKIPPED)
        flow.refresh_from_db()
        self.assertEqual(flow.status, FlowStatus.FAILED)

    def test_drift_after_approval_fails_the_action(self) -> None:
        flow = self._flow(name="exec")
        action = self.store.add_action(
            flow,
            action_type="provider.update",
            target_uuid="p1",
            values={"name": "x"},
            base_values={"name": "old"},
            base_etag="base-etag",
        )
        # Approved against a different state than the one found at execution
        self.store.approve_flow(flow, admin=self.other, approved_etags={action.uuid: "approved-etag"})

        fake = _FakeActionType(fingerprint="changed-after-approval")
        with mock.patch("uds.mutability.registry.get", return_value=fake):
            summary = execute_flow(flow, request=object())

        self.assertEqual(summary["status"], FlowStatus.FAILED)
        action.refresh_from_db()
        self.assertEqual(action.status, FlowActionStatus.FAILED)
        self.assertIn("changed after approval", str(action.properties.get("result")))

    def test_force_policy_skips_execution_cas(self) -> None:
        flow = self._flow(name="exec")
        action = self.store.add_action(
            flow,
            action_type="provider.update",
            target_uuid="p1",
            values={"name": "x"},
            base_values={"name": "old"},
            base_etag="base-etag",
        )
        self.store.approve_flow(flow, admin=self.other, approved_etags={action.uuid: "approved-etag"})

        class _ForceType(_FakeActionType):
            stale_policy = StalePolicy.FORCE

        fake = _ForceType(fingerprint="anything-goes")
        with mock.patch("uds.mutability.registry.get", return_value=fake):
            summary = execute_flow(flow, request=object())

        self.assertEqual(summary["status"], FlowStatus.EXECUTED)
        self.assertEqual(fake.executed, ["p1"])

    def test_refuses_non_approved_flow(self) -> None:
        flow = self._flow(name="pending")
        self._action(flow)
        with self.assertRaises(ValueError):
            execute_flow(flow, request=object())
