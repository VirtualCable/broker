"""Execution of approved flows.

Runs every approved action of a flow in strict ``order`` through its
action type's canonical REST machinery (as the approving administrator),
recording each outcome on the action. The store owns the state machine:
this module only drives it, honoring the action types'
:class:`~uds.mutability.base.StalePolicy` with a final CAS check right
before each run (the state may change between approval and execution).

All flow/bookkeeping work is synchronous (Django ORM); the ``async``
boundary wraps exactly one ``action_type.execute`` call at a time, so
types keep using their own ``sync_to_async`` machinery as they do on
the MCP surface.
"""

import asyncio
import logging
import typing

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
    for action in flow.actions.filter(status=FlowActionStatus.APPROVED).order_by("order"):
        store.mark_action_executing(action)
        try:
            action_type = registry.get(action.action_type)
            if action_type is None:
                raise ValueError(f"unknown action type {action.action_type}")
            # Final CAS check: the target must still match what was
            # approved. FORCE types skip it by design (their execute is
            # an idempotent set of the approved payload).
            if action_type.stale_policy == StalePolicy.DENY:
                target = action_type.resolve_target(action.target_uuid)
                expected = action.approved_etag or action.base_etag
                if action_type.fingerprint(target) != expected:
                    raise ValueError("target changed after approval; rejecting stale execution")
            summary = asyncio.run(action_type.execute(action, request))
        except Exception as e:
            logger.warning("Action %s (%s) failed: %s", action.uuid, action.action_type, e)
            store.mark_action_result(action, result=str(e), failed=True)
            results.append({"id": action.uuid, "order": action.order, "ok": False, "error": str(e)})
            # The store has skipped the remaining approved actions already
            break
        store.mark_action_result(action, result=summary)
        results.append({"id": action.uuid, "order": action.order, "ok": True, "result": summary})

    flow.refresh_from_db()
    return {"flow": flow.uuid, "status": str(flow.status), "results": results}
