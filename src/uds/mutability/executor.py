"""Execution of approved flows (second pass of the launch).

Runs every approved action of a flow in strict ``order`` through its
action type's canonical REST machinery (as the approving administrator),
recording each outcome on the action. The store owns the state machine:
this module only drives it.

The launch is two-pass (see ``FlowStore.verify_flow`` for pass 1, the
strict whole-item gate): right before each run, pass 2 re-checks ONLY
the touched fields against the frozen approval (``approved_values``) —
unrelated changes to the target do not block, touched-field drift does.
The state may change between approval and execution; this final check
catches the race (low probability, but reported: the error says which
action, which field, and the approved vs live values).

A drift caught mid-run revokes the action (REVOKED — the run cannot be
trusted, so nothing gets executed) and stops the flow like any failure;
plain execution errors mark the action FAILED. Both are recoverable:
the flow returns to LOCKED (the administrator's hands), the remaining
approved actions stay approved and only the failed/revoked ones need
re-approval or skip. The flow's ``run_note`` property records where the
run stopped and why.

All flow/bookkeeping work is synchronous (Django ORM); the ``async``
boundary wraps exactly one ``action_type.execute`` call at a time through
``async_to_sync`` (the same pattern the MCP surface uses): under ASGI the
REST handler's thread owns an asgiref ``CurrentThreadExecutor`` (Django
runs sync views thread-sensitively), and plain ``asyncio.run`` there
would make the action types' ``sync_to_async(thread_sensitive=True)``
submit onto their own thread ("You cannot submit onto
CurrentThreadExecutor from its own thread"). ``async_to_sync`` stacks its
own executor and pumps it, so the sync work runs back on the request
thread — same connection the request uses.
"""

import logging
import typing

from asgiref.sync import async_to_sync

from uds.core.types.mcp import FlowActionStatus, FlowStatus
from uds.mutability.base import JsonObject, StalePolicy
from uds.mutability.store import FlowStore
from uds.models import ActionFlow

logger: logging.Logger = logging.getLogger(__name__)


def execute_flow(flow: ActionFlow, request: typing.Any) -> JsonObject:
    """Run an approved flow synchronously; returns an execution summary."""
    from uds.mutability import registry

    flow.refresh_from_db()
    if flow.status not in (FlowStatus.APPROVED, FlowStatus.EXECUTING):
        raise ValueError(f"Flow {flow.uuid} is {flow.status}, only approved flows can be executed")

    store = FlowStore()
    results: list[JsonObject] = []
    stop_note: str | None = None
    for action in flow.actions.filter(status=FlowActionStatus.APPROVED).order_by("order"):
        store.mark_action_executing(action)
        try:
            found = registry.get(action.action_type)
            if found is None:
                raise ValueError(f"unknown action type {action.action_type}")
            action_type = found()
            # Pass 2: only the touched fields must still match the frozen
            # approval. FORCE types skip it by design (their execute is an
            # idempotent set of the approved payload).
            stale: str | None = None
            if action_type.stale_policy == StalePolicy.DENY:
                try:
                    action_type.resolve_target(action.target_uuid)
                except Exception:
                    stale = f"target {action.target_uuid} no longer exists"
                else:
                    drifted = action_type.field_drift(action, ref_values=action.approved_values or {})
                    if drifted:
                        detail = "; ".join(
                            f"field {name!r} changed after approval "
                            f"(approved: {change['approved']!r}, live: {change['live']!r})"
                            for name, change in sorted(drifted.items())
                        )
                        stale = f"execution stopped: {detail}"
            if stale is not None:
                # The approved state no longer holds: revoke (never
                # execute) and stop, like any failure.
                logger.warning("Action %s (%s) revoked: %s", action.uuid, action.action_type, stale)
                store.revoke_action(action, reason=stale)
                results.append(
                    {"id": action.uuid, "order": action.order, "ok": False, "revoked": True, "error": stale}
                )
                stop_note = f"stopped at action {action.order} ({action.uuid}): {stale}"
                # The store returned the flow to locked; remaining approved
                # actions keep their approval for the retry
                break
            summary = async_to_sync(action_type.execute)(action, request)
        except Exception as e:
            logger.warning("Action %s (%s) failed: %s", action.uuid, action.action_type, e)
            store.mark_action_result(action, result=str(e), failed=True)
            results.append({"id": action.uuid, "order": action.order, "ok": False, "error": str(e)})
            stop_note = f"stopped at action {action.order} ({action.uuid}): {e}"
            # The store returned the flow to locked; remaining approved
            # actions keep their approval for the retry
            break
        store.mark_action_result(action, result=summary)
        results.append({"id": action.uuid, "order": action.order, "ok": True, "result": summary})

    if stop_note is not None:
        flow.properties["run_note"] = stop_note
    flow.refresh_from_db()
    return {"flow": flow.uuid, "status": str(flow.status), "results": results}
