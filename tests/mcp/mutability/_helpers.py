"""Shared helpers for the mutability tests."""

import typing

from uds.core.types.mcp import FlowActionStatus
from uds.models import FlowAction

MUTATION_TOOL_NAMES: typing.Final[tuple[str, ...]] = (
    "get_mutable_fields",
    "propose_provider_update",
    "propose_service_update",
    "list_pending_actions",
    "update_pending_action",
    "cancel_pending_action",
)


def build_action(**kwargs: typing.Any) -> FlowAction:
    """Build a minimal (unsaved) action for type-level tests."""
    return FlowAction(
        action_type=kwargs.get("action_type", "provider.update"),
        target_uuid=kwargs.get("target_uuid", "target-1"),
        values=kwargs.get("values", {"name": "new name"}),
        base_values=kwargs.get("base_values", {"name": "old name"}),
        base_etag=kwargs.get("base_etag", "abc123"),
        status=FlowActionStatus.PENDING,
    )
