"""Persistence and lifecycle for supervised-mutability flows.

Every agent proposal lives in the ``uds_action_flow`` / ``uds_flow_action``
tables (see :mod:`uds.models.action_flow`). The store is the application
layer on top of those plain models: caps (``uds.core.consts.mcp``), lazy
TTL expiry, and the guarded lifecycle transitions.

Design (doc/plan/mcp-mutability.md):

- Proposals are conditional swaps (CAS): the server snapshots the current
  values of the touched fields (``base_values``) and the whole item
  fingerprint (``base_etag``) at proposal time, on the same row as the
  payload.
- Decisions (approve/reject/cancel/expire) are taken at the *flow* level.
  When a flow is decided, its still-pending actions are skipped, so no
  decided flow ever has pending actions.
- Decided states are terminal. Nobody re-opens a decided flow.
"""

import datetime
import typing

from uds.core.consts import mcp as consts_mcp
from uds.core.types.mcp import FlowActionStatus, FlowStatus
from uds.core.util.model import sql_now
from uds.models import ActionFlow, FlowAction, User
from uds.mutability.base import StalePolicy


class MutabilityError(ValueError):
    """Base error for the mutability subsystem."""


class InvalidTransition(MutabilityError):
    """A flow or action cannot move to the requested state."""


class NotActionOwner(MutabilityError):
    """The actor is not the owner of the flow."""


class StaleProposal(MutabilityError):
    """The target drifted from the proposal base (or cannot be verified)."""


class FlowStore:
    """Create, query and transition proposal flows and their actions."""

    # ------------------------------------------------------------ creation

    def create_flow(
        self,
        *,
        owner: User,
        agent: str = "",
        name: str = "",
        justification: str = "",
        ttl: datetime.timedelta | None = None,
    ) -> ActionFlow:
        """Create a pending flow, enforcing the per-user pending cap.

        ``ttl`` is the proposer's expected resolution window; it defaults
        to :data:`consts_mcp.FLOW_TTL_DAYS`.
        """
        if self.count_pending_flows(owner_uuid=owner.uuid) >= consts_mcp.MAX_FLOWS_PER_USER:
            raise MutabilityError(
                f"Too many pending proposals (limit {consts_mcp.MAX_FLOWS_PER_USER}). "
                "Cancel some before proposing more."
            )
        flow = ActionFlow.objects.create(
            owner=owner,
            name=name,
            justification=justification,
            status=FlowStatus.PENDING,
            due_date=sql_now() + (ttl or datetime.timedelta(days=consts_mcp.FLOW_TTL_DAYS)),
        )
        if agent:
            # Informational: which agent (clientInfo) created the flow
            flow.properties["agent"] = agent
        return flow

    def add_action(
        self,
        flow: ActionFlow,
        *,
        action_type: str,
        target_uuid: str,
        values: dict[str, typing.Any],
        base_values: dict[str, typing.Any],
        base_etag: str,
        justification: str = "",
    ) -> FlowAction:
        """Append one action to a pending flow, enforcing the size cap."""
        if flow.status != FlowStatus.PENDING:
            raise InvalidTransition(f"Flow {flow.uuid} is {flow.status}, only pending flows accept actions")
        last = flow.actions.order_by("-order").values_list("order", flat=True).first()
        if last is not None and last + 1 > consts_mcp.MAX_ACTIONS_PER_FLOW:
            raise MutabilityError(
                f"Too many actions in this proposal (limit {consts_mcp.MAX_ACTIONS_PER_FLOW})."
            )
        return FlowAction.objects.create(
            flow=flow,
            order=(last or 0) + 1,
            action_type=action_type,
            target_kind=self._target_kind(action_type),
            target_uuid=target_uuid,
            justification=justification,
            values=values,
            base_values=base_values,
            base_etag=base_etag,
            status=FlowActionStatus.PENDING,
        )

    def rebase_action(
        self,
        action: FlowAction,
        *,
        values: dict[str, typing.Any],
        base_values: dict[str, typing.Any],
        base_etag: str,
        justification: str = "",
    ) -> FlowAction:
        """Replace the payload of a pending action (proposer edit)."""
        if action.status != FlowActionStatus.PENDING:
            raise InvalidTransition(
                f"Action {action.uuid} is {action.status}, only pending actions can be edited"
            )
        action.values = values
        action.base_values = base_values
        action.base_etag = base_etag
        action.justification = justification
        action.save(update_fields=["values", "base_values", "base_etag", "justification"])
        return action

    # -------------------------------------------------------------- queries

    def get_flow(self, flow_uuid: str) -> ActionFlow | None:
        """Return the flow with this uuid (lazily expired), or None."""
        flow: ActionFlow | None = ActionFlow.objects.filter(uuid=flow_uuid).first()
        if flow is None:
            return None
        self.maybe_expire_flow(flow)
        return flow

    def get_action(self, action_uuid: str) -> FlowAction | None:
        """Return the action with this uuid (lazily expired), or None."""
        action: FlowAction | None = FlowAction.objects.select_related("flow").filter(uuid=action_uuid).first()
        if action is None:
            return None
        self.maybe_expire_flow(action.flow)
        action.refresh_from_db()
        return action

    def list_flows(
        self,
        *,
        status: FlowStatus | None = None,
        owner_uuid: str | None = None,
    ) -> list[ActionFlow]:
        """List flows (oldest first), optionally filtered, lazily expiring."""
        qs = ActionFlow.objects.all()
        if status is not None:
            qs = qs.filter(status=status)
        if owner_uuid is not None:
            qs = qs.filter(owner__uuid=owner_uuid)
        result: list[ActionFlow] = []
        for flow in qs.order_by("created"):
            if self.maybe_expire_flow(flow):
                continue
            result.append(flow)
        return result

    def list_actions(
        self,
        *,
        owner_uuid: str | None = None,
        action_type: str | None = None,
    ) -> list[FlowAction]:
        """List actions of a user's flows, in flow/order sequence."""
        qs = FlowAction.objects.select_related("flow").order_by("flow__created", "flow__id", "order")
        if owner_uuid is not None:
            qs = qs.filter(flow__owner__uuid=owner_uuid)
        if action_type is not None:
            qs = qs.filter(action_type=action_type)
        result: list[FlowAction] = []
        for action in qs:
            if self.maybe_expire_flow(action.flow):
                continue
            result.append(action)
        return result

    def count_pending_flows(self, *, owner_uuid: str) -> int:
        """Number of still-pending flows of one user (spam cap)."""
        return ActionFlow.objects.filter(owner__uuid=owner_uuid, status=FlowStatus.PENDING).count()

    # ----------------------------------------------------------- transitions

    def cancel_flow(self, flow: ActionFlow, *, actor_uuid: str) -> None:
        """Owner withdraws its own pending flow."""
        if flow.owner is None or flow.owner.uuid != actor_uuid:
            raise NotActionOwner(f"Flow {flow.uuid} does not belong to user {actor_uuid}")
        self._decide_flow(flow, FlowStatus.CANCELLED, decided_by=actor_uuid, note="Cancelled by proposer")

    def reopen_flow(self, flow: ActionFlow, *, actor_uuid: str, ttl: datetime.timedelta) -> None:
        """Proposer revives its own expired flow, within the grace window.

        The whole proposal comes back: skipped actions turn pending again
        and the expiration moves to ``now + ttl``. Cancelled/rejected (a
        conscious decision) and flows expired beyond
        :data:`consts_mcp.REOPEN_GRACE_DAYS` stay final.
        """
        if flow.owner is None or str(flow.owner.uuid) != actor_uuid:
            raise NotActionOwner(f"Flow {flow.uuid} does not belong to user {actor_uuid}")
        if flow.status != FlowStatus.EXPIRED:
            raise InvalidTransition(f"Flow {flow.uuid} is {flow.status}, only expired flows can be reopened")
        if flow.due_date is None or sql_now() - flow.due_date > datetime.timedelta(
            days=consts_mcp.REOPEN_GRACE_DAYS
        ):
            raise InvalidTransition(
                f"Flow {flow.uuid} expired more than {consts_mcp.REOPEN_GRACE_DAYS} days ago "
                "and can no longer be reopened"
            )
        flow.status = FlowStatus.PENDING
        flow.due_date = sql_now() + ttl
        flow.save(update_fields=["status", "due_date"])
        flow.actions.filter(status=FlowActionStatus.SKIPPED).update(status=FlowActionStatus.PENDING)

    def reject_flow(self, flow: ActionFlow, *, admin: str, reason: str | None = None) -> None:
        """Administrator declines a pending flow."""
        self._decide_flow(flow, FlowStatus.REJECTED, decided_by=admin, note=reason or "Rejected")

    def approval_etags(self, flow: ActionFlow) -> dict[str, str]:
        """CAS check at approval time: live fingerprint per pending action.

        For every still-pending action the whole-item fingerprint is
        recomputed now. Comparing it with ``base_etag`` enforces the
        action type's :class:`~uds.mutability.base.StalePolicy`:

        - ``DENY`` (default): any drift raises :class:`StaleProposal`
          and the whole approval is refused; the administrator must ask
          the proposer for a fresh proposal.
        - ``FORCE``: drift is tolerated; the fresh fingerprint is still
          recorded (see :meth:`approve_flow`) so execution can detect
          later changes.

        Unverifiable actions (unknown action type, vanished target)
        raise :class:`StaleProposal` regardless of the policy: approving
        what cannot be checked would defeat the whole CAS scheme.

        Returns ``{action_uuid: fingerprint}`` for the pending actions.
        """
        from uds.mutability import registry

        etags: dict[str, str] = {}
        stale: list[str] = []
        for action in flow.actions.filter(status=FlowActionStatus.PENDING).order_by("order"):
            action_type = registry.get(action.action_type)
            if action_type is None:
                stale.append(f"action {action.uuid}: unknown action type {action.action_type}")
                continue
            try:
                target = action_type.resolve_target(action.target_uuid)
            except Exception:
                stale.append(f"action {action.uuid}: target {action.target_uuid} no longer exists")
                continue
            fingerprint = action_type.fingerprint(target)
            etags[action.uuid] = fingerprint
            if fingerprint != action.base_etag and action_type.stale_policy == StalePolicy.DENY:
                stale.append(f"action {action.uuid}: target changed since it was proposed")
        if stale:
            raise StaleProposal("; ".join(stale))
        return etags

    def approve_flow(
        self, flow: ActionFlow, *, admin: User, approved_etags: dict[str, str] | None = None
    ) -> None:
        """Administrator accepts a pending flow; execution follows.

        Every pending action becomes approved, so the executor can run
        them in ``order``. The flow reaches EXECUTED/FAILED afterwards.

        ``approved_etags`` (as returned by :meth:`approval_etags`) is
        stored on each action: it freezes the state the administrator
        actually approved, and the executor compares against it before
        running, so changes landing between approval and execution are
        detected too.
        """
        if flow.status != FlowStatus.PENDING:
            raise InvalidTransition(f"Flow {flow.uuid} is {flow.status}, only pending flows can be approved")
        flow.status = FlowStatus.APPROVED
        flow.approved_by = admin
        flow.approved_at = sql_now()
        flow.save(update_fields=["status", "approved_by", "approved_at"])
        for action in flow.actions.filter(status=FlowActionStatus.PENDING):
            if approved_etags and action.uuid in approved_etags:
                action.approved_etag = approved_etags[action.uuid]
                action.save(update_fields=["approved_etag"])
        flow.actions.filter(status=FlowActionStatus.PENDING).update(status=FlowActionStatus.APPROVED)

    def mark_action_executing(self, action: FlowAction) -> None:
        if action.status != FlowActionStatus.APPROVED:
            raise InvalidTransition(
                f"Action {action.uuid} is {action.status}, only approved actions can execute"
            )
        action.status = FlowActionStatus.EXECUTING
        action.save(update_fields=["status"])

    def mark_action_result(self, action: FlowAction, *, result: str, failed: bool = False) -> None:
        """Record the execution summary of an action, ending its run."""
        if action.status != FlowActionStatus.EXECUTING:
            raise InvalidTransition(
                f"Action {action.uuid} is {action.status}, result requires an executing action"
            )
        action.status = FlowActionStatus.FAILED if failed else FlowActionStatus.EXECUTED
        action.properties["result"] = result
        action.save(update_fields=["status"])
        self._update_flow_execution_state(action.flow)

    # ------------------------------------------------------------- expiry

    def maybe_expire_flow(self, flow: ActionFlow) -> bool:
        """Expire (and persist) the flow if its due date is gone."""
        if flow.status != FlowStatus.PENDING:
            return False
        if flow.due_date is None or sql_now() <= flow.due_date:
            return False
        self._decide_flow(flow, FlowStatus.EXPIRED, decided_by=None, note="Expired")
        return True

    # ------------------------------------------------------------- helpers

    def _update_flow_execution_state(self, flow: ActionFlow) -> None:
        """Aggregate the flow state from its actions after each run step.

        While actions remain to run the flow stays ``EXECUTING``; a
        failure stops the run (remaining pending actions are skipped)
        and marks the flow ``FAILED``; otherwise the last result decides
        between ``EXECUTED`` and ``FAILED``.
        """
        actions = flow.actions.all()
        if actions.filter(status=FlowActionStatus.EXECUTING).exists():
            return  # another step is still running; flow state settles later
        # Approved actions are still to be run (PENDING ones should not
        # exist on an approved flow, but are honoured all the same)
        to_run = FlowActionStatus.PENDING, FlowActionStatus.APPROVED
        if not actions.filter(status__in=to_run).exists():
            final = (
                FlowStatus.FAILED
                if actions.filter(status=FlowActionStatus.FAILED).exists()
                else FlowStatus.EXECUTED
            )
            if flow.status != final:
                flow.status = final
                flow.save(update_fields=["status"])
            return
        if actions.filter(status=FlowActionStatus.FAILED).exists():
            # Stop-on-failure: whatever is left will not run
            actions.filter(status__in=to_run).update(status=FlowActionStatus.SKIPPED)
            if flow.status != FlowStatus.FAILED:
                flow.status = FlowStatus.FAILED
                flow.save(update_fields=["status"])
            return
        if flow.status != FlowStatus.EXECUTING:
            flow.status = FlowStatus.EXECUTING
            flow.save(update_fields=["status"])

    def _decide_flow(
        self,
        flow: ActionFlow,
        status: FlowStatus,
        *,
        decided_by: str | None,
        note: str | None,
    ) -> None:
        """Move a pending flow to a decided state and skip its pending actions."""
        if flow.status != FlowStatus.PENDING:
            raise InvalidTransition(
                f"Flow {flow.uuid} is {flow.status}, only pending flows allow this operation"
            )
        flow.status = status
        flow.save(update_fields=["status"])
        # decided_by/decided note are auxiliary (only approvals get columns)
        if decided_by is not None:
            flow.properties["decided_by"] = decided_by
        if note is not None:
            flow.properties["decided_note"] = note
        flow.actions.filter(status=FlowActionStatus.PENDING).update(status=FlowActionStatus.SKIPPED)

    @staticmethod
    def _target_kind(action_type: str) -> str:
        """Target family of an action type id (e.g. ``provider.update`` -> ``provider``)."""
        return action_type.split(".")[0]
