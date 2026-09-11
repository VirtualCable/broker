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
- Approval is one-step, per action: an administrator *approves* an
  action (fresh display snapshot in ``snap_info``, live
  fingerprint/values frozen into ``approved_etag``/``approved_values``,
  status APPROVED). Approving an action of a pending flow acquires the
  flow (LOCKED): it leaves the agent's view and can no longer be edited
  by its owner.
- Launch is two-pass: pass 1 (``verify_flow``) is the strict gate — any
  whole-item change since approval revokes the action (REVOKED, the only
  system-initiated revocation) and refuses the launch. Pass 2 (executor)
  re-checks only the touched fields right before each run: unrelated
  drift does not block, touched-field drift does.
- Failures are recoverable: a mid-run failure returns the flow to
  LOCKED (the administrator's hands), the remaining approved actions
  stay approved and only the failed/revoked ones need to be re-approved
  or skipped from the frontend.
- Decisions (approve/reject/cancel/expire) are taken at the *flow* level.
  When a flow is decided, its still-undecided actions are skipped, so no
  decided flow ever has undecided actions. Rejected/cancelled/expired
  and executed are terminal; nobody re-opens them (except the owner
  revival of an expired proposal within the grace window).
"""

import datetime
import typing

from uds.core.consts import mcp as consts_mcp
from uds.core.types.mcp import FlowActionStatus, FlowStatus
from uds.core.util.model import sql_now
from uds.models import ActionFlow, FlowAction, User
from uds.mutability.base import MutableActionType, StalePolicy


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
        action = FlowAction.objects.create(
            flow=flow,
            order=(last or 0) + 1,
            action_type=action_type,
            target_kind=self._target_kind(action_type),
            target_uuid=target_uuid,
            justification=justification,
            values=values,
            status=FlowActionStatus.PENDING,
        )
        # Dynamic CAS data lives on Properties (kept out of the row)
        action.base_values = base_values
        action.base_etag = base_etag
        return action

    def rebase_action(
        self,
        action: FlowAction,
        *,
        values: dict[str, typing.Any],
        base_values: dict[str, typing.Any],
        base_etag: str,
        justification: str = "",
    ) -> FlowAction:
        """Replace the payload of a pending action (proposer edit).

        Only allowed while the flow is still pending (a locked flow is
        in the administrator's hands): once the admin touches the flow
        the agent cannot edit anything. Failed/revoked actions are the
        administrator's to recover (re-approve or skip), never the
        proposer's.
        """
        if action.flow.status != FlowStatus.PENDING:
            raise InvalidTransition(
                f"Flow {action.flow.uuid} is {action.flow.status}, only actions of pending flows can be edited"
            )
        if action.status != FlowActionStatus.PENDING:
            raise InvalidTransition(
                f"Action {action.uuid} is {action.status}, only pending actions can be edited"
            )
        action.values = values
        action.justification = justification
        action.base_values = base_values
        action.base_etag = base_etag
        action.save(update_fields=["values", "justification"])
        # Fresh base: any stale display cache is void
        action.properties.pop("snap_info", None)
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
        """Owner withdraws its own pending flow.

        A locked flow is in the administrator's hands: the owner can no
        longer cancel it (it is invisible to the agent anyway).
        """
        if flow.owner is None or flow.owner.uuid != actor_uuid:
            raise NotActionOwner(f"Flow {flow.uuid} does not belong to user {actor_uuid}")
        self._decide_flow(
            flow,
            FlowStatus.CANCELLED,
            allowed=(FlowStatus.PENDING,),
            decided_by=actor_uuid,
            note="Cancelled by proposer",
        )

    def reopen_flow(self, flow: ActionFlow, *, actor_uuid: str, ttl: datetime.timedelta) -> None:
        """Proposer revives its own expired flow, within the grace window.

        The whole proposal comes back: skipped actions turn pending again
        and the expiration moves to ``now + ttl``. Cancelled/rejected (a
        conscious decision) and flows expired beyond
        :data:`consts_mcp.REOPEN_GRACE_DAYS` stay final.
        """
        if flow.owner is None or flow.owner.uuid != actor_uuid:
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
        # A pending action never carries a frozen approval nor a stale review
        for action in flow.actions.filter(status=FlowActionStatus.SKIPPED):
            action.status = FlowActionStatus.PENDING
            action.save(update_fields=["status"])
            action.properties.pop("snap_info", None)
            action.properties.pop("approved_etag", None)
            action.properties.pop("approved_values", None)

    def reject_flow(self, flow: ActionFlow, *, admin: str, reason: str | None = None) -> None:
        """Administrator declines a pending or locked flow (no way back)."""
        self._decide_flow(
            flow,
            FlowStatus.REJECTED,
            allowed=(FlowStatus.PENDING, FlowStatus.LOCKED),
            decided_by=admin,
            note=reason or "Rejected",
        )

    # ------------------------------------------------- lock / approve / skip

    def _action_type(self, action: FlowAction) -> MutableActionType:
        """Action type of an action, or StaleProposal when it is unknown."""
        from uds.mutability import registry

        found = registry.get(action.action_type)
        if found is None:
            raise StaleProposal(f"action {action.uuid}: unknown action type {action.action_type}")
        return found()

    def _editable_flow(self, flow: ActionFlow) -> None:
        """Guard: admin operations on actions run on pending or locked flows."""
        if flow.status not in (FlowStatus.PENDING, FlowStatus.LOCKED):
            raise InvalidTransition(f"Flow {flow.uuid} is {flow.status}, only pending or locked flows allow it")

    def _acquire_flow(self, flow: ActionFlow, *, admin: User) -> None:
        """Move a pending flow to LOCKED (first admin touch acquires it).

        A locked flow leaves the agent's view and can no longer be
        edited by its owner, so the review happens on stable data.
        """
        if flow.status == FlowStatus.PENDING:
            flow.status = FlowStatus.LOCKED
            flow.save(update_fields=["status"])
            flow.properties["locked_by"] = admin.name

    def lock_flow(self, flow: ActionFlow, *, admin: User) -> None:
        """Administrator acquires a pending flow for review.

        The flow leaves the agent's visibility and its owner can no
        longer edit it (nor cancel it). Locking an already locked flow
        is a no-op; anything past ``LOCKED`` is refused.
        """
        if flow.status == FlowStatus.LOCKED:
            return
        if flow.status != FlowStatus.PENDING:
            raise InvalidTransition(f"Flow {flow.uuid} is {flow.status}, only pending flows can be locked")
        flow.status = FlowStatus.LOCKED
        flow.save(update_fields=["status"])
        flow.properties["locked_by"] = admin.name

    def approve_action(self, action: FlowAction, *, admin: User) -> FlowAction:
        """Administrator accepts an action, freezing the live state.

        This is the review: the display snapshot (``snap_info``) and the
        CAS reference (``approved_etag``/``approved_values``) are taken
        in the same step, so what the administrator saw is exactly what
        the launch will verify against. Approving a pending flow
        acquires it (LOCKED). Allowed on PENDING, APPROVED (re-approve
        refreshes the freeze), FAILED and REVOKED actions. EXECUTED and
        SKIPPED ones cannot be re-approved: redoing an invasive
        operation is a conscious new proposal, never a relaunch.
        """
        self._editable_flow(action.flow)
        if action.status not in (
            FlowActionStatus.PENDING,
            FlowActionStatus.APPROVED,
            FlowActionStatus.FAILED,
            FlowActionStatus.REVOKED,
        ):
            raise InvalidTransition(f"Action {action.uuid} is {action.status}, it cannot be approved")
        action_type = self._action_type(action)
        try:
            snap = action_type.approval_snapshot(action)
            target = action_type.resolve_target(action.target_uuid)
        except Exception:
            raise StaleProposal(f"action {action.uuid}: target {action.target_uuid} no longer exists") from None
        snap["approved_by"] = admin.name
        snap["approved_at"] = sql_now().isoformat()
        action.snap_info = snap
        action.approved_etag = action_type.fingerprint(target)
        action.approved_values = action_type.snapshot_values(
            target, list(action_type.flatten_values(action.values))
        )
        action.status = FlowActionStatus.APPROVED
        action.save(update_fields=["status"])
        self._acquire_flow(action.flow, admin=admin)
        return action

    def skip_action(self, action: FlowAction, *, admin: User) -> FlowAction:
        """Administrator marks an action as not-to-run.

        Allowed on PENDING, APPROVED, FAILED and REVOKED actions: the
        admin decides this one will not execute when the flow is
        launched. The flow approval only requires the rest to be
        approved. Approving a pending flow acquires it (LOCKED).
        """
        self._editable_flow(action.flow)
        if action.status not in (
            FlowActionStatus.PENDING,
            FlowActionStatus.APPROVED,
            FlowActionStatus.FAILED,
            FlowActionStatus.REVOKED,
        ):
            raise InvalidTransition(f"Action {action.uuid} is {action.status}, it cannot be skipped")
        action.status = FlowActionStatus.SKIPPED
        action.save(update_fields=["status"])
        # The frozen approval (if any) is meaningless for a skipped action
        action.properties.pop("approved_etag", None)
        action.properties.pop("approved_values", None)
        self._acquire_flow(action.flow, admin=admin)
        return action

    def compliance(self, action: FlowAction) -> str:
        """Live drift indicator of one action: ``ok``/``outdated``/``conflict``.

        - ``ok``: the reference snapshot still matches the live item.
        - ``outdated``: the item changed, but none of the fields this
          action touches did.
        - ``conflict``: a touched field changed, or the action cannot be
          verified (unknown type, vanished target).

        APPROVED/REVOKED actions are compared against their frozen
        approval, the rest against the proposal base; actions already
        running, done, failed or skipped simply report ``ok`` (nothing
        to decide).
        """
        if action.status in (
            FlowActionStatus.EXECUTING,
            FlowActionStatus.EXECUTED,
            FlowActionStatus.FAILED,
            FlowActionStatus.SKIPPED,
        ):
            return "ok"
        action_type = self._action_type(action)
        ref_etag: str
        ref_values: dict[str, typing.Any]
        if action.status in (FlowActionStatus.APPROVED, FlowActionStatus.REVOKED):
            ref_etag = action.approved_etag
            ref_values = action.approved_values or {}
        else:
            ref_etag = action.base_etag
            ref_values = action.base_values or {}
        if not ref_etag:
            return "conflict"
        return action_type.drift_against(action, ref_etag=ref_etag, ref_values=ref_values)

    def flow_compliance(self, flow: ActionFlow) -> str:
        """Aggregate compliance of a flow: the worst of its actions.

        ``conflict`` when any action conflicts, else ``outdated`` when
        any action is outdated, else ``ok``. Empty flows report ``ok``.
        """
        worst = "ok"
        for action in flow.actions.all():
            level = self.compliance(action)
            if level == "conflict":
                return "conflict"
            if level == "outdated":
                worst = "outdated"
        return worst

    def verify_flow(self, flow: ActionFlow) -> list[FlowAction]:
        """Pass 1 of the launch: strict CAS re-check of every approved action.

        The flow must be pending or locked, have actions, and all of
        them must be approved, skipped or already executed — a
        partially-run flow relaunches without repeating the executed
        actions (they cannot be re-approved either: redoing an invasive
        operation is a conscious new proposal, never a relaunch). At
        least one action must be approved, or there would be nothing to
        run. Every approved action is re-verified against its frozen
        ``approved_etag`` — the WHOLE item, not just the touched
        fields: the administrator approved a view of the target, so any
        change invalidates it and the launch is refused.

        Drifted actions are revoked (recoverable: re-approve or skip
        them); the flow stays as it was. FORCE types tolerate drift
        (their execution is an idempotent set of the approved payload).
        Unverifiable actions (unknown type, vanished target) are revoked
        regardless of the policy: running what cannot be checked would
        defeat the whole CAS scheme.

        Returns the revoked actions (empty when the flow is ready).
        """
        self._editable_flow(flow)
        actions = list(flow.actions.order_by("order"))
        if not actions:
            raise InvalidTransition(f"Flow {flow.uuid} has no actions to approve")
        ready = FlowActionStatus.APPROVED, FlowActionStatus.SKIPPED, FlowActionStatus.EXECUTED
        not_ready = [a for a in actions if a.status not in ready]
        if not_ready:
            detail = ", ".join(f"{a.uuid} ({a.status})" for a in not_ready)
            raise InvalidTransition(f"Actions must be approved, skipped or executed first: {detail}")
        if not any(a.status == FlowActionStatus.APPROVED for a in actions):
            raise InvalidTransition(f"Flow {flow.uuid} has no approved actions to execute")
        revoked: list[FlowAction] = []
        for action in actions:
            if action.status != FlowActionStatus.APPROVED:
                continue
            reason = self._drift_reason(action)
            if reason is not None:
                self.revoke_action(action, reason=reason)
                revoked.append(action)
        return revoked

    def approve_flow(self, flow: ActionFlow, *, admin: User) -> None:
        """Administrator accepts a flow ready to run (pass 1 must have passed).

        Every action must be approved, skipped or already executed (at
        least one approved); the caller runs :meth:`verify_flow` first
        and refuses the launch when actions got revoked. Allowed on
        pending and locked flows (a direct pending -> approved launch
        keeps the door open for future automations). The flow ends
        EXECUTED or back to LOCKED (recoverable failure) afterwards.
        """
        self._editable_flow(flow)
        actions = list(flow.actions.all())
        if not actions:
            raise InvalidTransition(f"Flow {flow.uuid} has no actions to approve")
        ready = FlowActionStatus.APPROVED, FlowActionStatus.SKIPPED, FlowActionStatus.EXECUTED
        not_ready = [a for a in actions if a.status not in ready]
        if not_ready:
            detail = ", ".join(f"{a.uuid} ({a.status})" for a in not_ready)
            raise InvalidTransition(f"Actions must be approved, skipped or executed first: {detail}")
        if not any(a.status == FlowActionStatus.APPROVED for a in actions):
            raise InvalidTransition(f"Flow {flow.uuid} has no approved actions to execute")
        flow.status = FlowStatus.APPROVED
        flow.approved_by = admin
        flow.approved_at = sql_now()
        flow.save(update_fields=["status", "approved_by", "approved_at"])

    def revoke_action(self, action: FlowAction, *, reason: str) -> None:
        """System-only revocation: an approved/executing action drifted (CAS).

        The frozen approval is kept: it is the reference the compliance
        indicator compares against, so the administrator sees what
        drifted. Revocation is recoverable: the administrator re-approves
        the action (fresh freeze) or skips it. While verifying a pending
        or locked flow nothing else changes; during execution the run
        stops at the revocation and the flow returns to LOCKED.
        """
        if action.status not in (FlowActionStatus.APPROVED, FlowActionStatus.EXECUTING):
            raise InvalidTransition(
                f"Action {action.uuid} is {action.status}, only approved or executing actions can be revoked"
            )
        was_executing = action.status == FlowActionStatus.EXECUTING
        action.status = FlowActionStatus.REVOKED
        action.save(update_fields=["status"])
        action.properties["result"] = reason
        if was_executing:
            self._update_flow_execution_state(action.flow)

    def _drift_reason(self, action: FlowAction) -> str | None:
        """Why an approved action can no longer be trusted (None when fine)."""
        from uds.mutability import registry

        found = registry.get(action.action_type)
        if found is None:
            return f"unknown action type {action.action_type}"
        action_type = found()
        try:
            target = action_type.resolve_target(action.target_uuid)
        except Exception:
            return f"target {action.target_uuid} no longer exists"
        if action_type.stale_policy == StalePolicy.FORCE:
            return None
        if action_type.fingerprint(target) != (action.approved_etag or action.base_etag):
            return "target changed after the action was approved"
        return None

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
        self._decide_flow(
            flow, FlowStatus.EXPIRED, allowed=(FlowStatus.PENDING,), decided_by=None, note="Expired"
        )
        return True

    # ------------------------------------------------------------- helpers

    def _update_flow_execution_state(self, flow: ActionFlow) -> None:
        """Aggregate the flow state from its actions after each run step.

        While actions remain to run the flow stays ``EXECUTING``; a
        failure or a revocation (CAS drift caught mid-run) stops the run
        and returns the flow to ``LOCKED`` — recoverable: the remaining
        approved actions STAY approved (the retry composes on its own,
        since only approved actions run and executed ones never repeat),
        and only the failed/revoked ones need the administrator. When
        nothing is left to run the flow ends ``EXECUTED``.
        """
        actions = flow.actions.all()
        if actions.filter(status=FlowActionStatus.EXECUTING).exists():
            return  # another step is still running; flow state settles later
        failed = (FlowActionStatus.FAILED, FlowActionStatus.REVOKED)
        if actions.filter(status__in=failed).exists():
            # Stop-on-failure: back to the administrator's hands. The
            # remaining approved actions keep their approval for the retry.
            if flow.status != FlowStatus.LOCKED:
                flow.status = FlowStatus.LOCKED
                flow.save(update_fields=["status"])
            return
        if actions.filter(status__in=(FlowActionStatus.PENDING, FlowActionStatus.APPROVED)).exists():
            if flow.status != FlowStatus.EXECUTING:
                flow.status = FlowStatus.EXECUTING
                flow.save(update_fields=["status"])
            return
        if flow.status != FlowStatus.EXECUTED:
            flow.status = FlowStatus.EXECUTED
            flow.save(update_fields=["status"])

    def _decide_flow(
        self,
        flow: ActionFlow,
        status: FlowStatus,
        *,
        allowed: tuple[FlowStatus, ...],
        decided_by: str | None,
        note: str | None,
    ) -> None:
        """Move a flow to a decided state and skip its undecided actions.

        ``allowed`` lists the source states (cancel/expire only accept a
        pending flow; reject also accepts a locked one). Undecided here
        means every action that has not run nor been decided: pending,
        approved, failed and revoked ones become skipped.
        """
        if flow.status not in allowed:
            raise InvalidTransition(
                f"Flow {flow.uuid} is {flow.status}, only {', '.join(allowed)} flows allow this operation"
            )
        flow.status = status
        flow.save(update_fields=["status"])
        # decided_by/decided note are auxiliary (only approvals get columns)
        if decided_by is not None:
            flow.properties["decided_by"] = decided_by
        if note is not None:
            flow.properties["decided_note"] = note
        flow.actions.filter(
            status__in=(
                FlowActionStatus.PENDING,
                FlowActionStatus.APPROVED,
                FlowActionStatus.FAILED,
                FlowActionStatus.REVOKED,
            )
        ).update(status=FlowActionStatus.SKIPPED)

    @staticmethod
    def _target_kind(action_type: str) -> str:
        """Target family of an action type id (e.g. ``provider.update`` -> ``provider``)."""
        return action_type.split(".")[0]
