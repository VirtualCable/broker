"""Shared helpers for the mutability tests."""

import typing

from uds.core.types.mcp import FlowActionStatus
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.models import ActionFlow, FlowAction
from uds.mutability import FlowStore

from tests.fixtures.authenticators import create_db_authenticator, create_db_users
from tests.utils.test import UDSTestCase


def make_request() -> ExtendedHttpRequestWithUser:
    """Bare request stub: executors and the proxy only carry it around
    in the mocked paths these tests exercise."""
    return ExtendedHttpRequestWithUser()


MUTATION_TOOL_NAMES: typing.Final[tuple[str, ...]] = (
    "get_mutable_fields",
    "propose_provider_update",
    "propose_service_update",
    "propose_config_update",
    "propose_network_update",
    "propose_calendar_update",
    "propose_calendar_rule_update",
    "propose_service_pool_group_update",
    "propose_account_update",
    "propose_transport_update",
    "propose_osmanager_update",
    "propose_mfa_update",
    "propose_notifier_update",
    "propose_authenticator_update",
    "propose_user_update",
    "propose_group_update",
    "propose_servicepool_update",
    "propose_metapool_update",
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


class FlowTestCase(UDSTestCase):
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
