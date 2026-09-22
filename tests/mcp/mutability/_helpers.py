"""Shared helpers for the mutability tests."""

import typing

from uds.core.types.mcp import FlowActionStatus
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.models import ActionFlow, FlowAction, User
from uds.mutability import FlowStore

from tests.fixtures.authenticators import create_db_authenticator, create_db_users
from tests.utils.test import UDSTestCase


def make_request(user: User | None = None) -> ExtendedHttpRequestWithUser:
    """Bare request stub: executors and the proxy only carry it around
    in the mocked paths these tests exercise. ``user`` is attached when
    given (the executor records it as the launch audit)."""
    request = ExtendedHttpRequestWithUser()
    if user is not None:
        request.user = user
    return request


MUTATION_TOOL_NAMES: typing.Final[tuple[str, ...]] = (
    "get_mutable_fields",
    "propose_provider_update",
    "propose_provider_create",
    "propose_provider_delete",
    "propose_service_update",
    "propose_service_create",
    "propose_config_update",
    "propose_network_update",
    "propose_network_create",
    "propose_network_delete",
    "propose_calendar_update",
    "propose_calendar_create",
    "propose_calendar_delete",
    "propose_calendar_rule_update",
    "propose_calendar_rule_create",
    "propose_calendar_rule_delete",
    "propose_service_pool_group_update",
    "propose_service_pool_group_create",
    "propose_service_pool_group_delete",
    "propose_image_create",
    "propose_image_delete",
    "propose_account_update",
    "propose_account_create",
    "propose_account_delete",
    "propose_transport_update",
    "propose_transport_create",
    "propose_transport_delete",
    "propose_osmanager_update",
    "propose_osmanager_create",
    "propose_osmanager_delete",
    "propose_mfa_update",
    "propose_notifier_update",
    "propose_authenticator_update",
    "propose_authenticator_create",
    "propose_authenticator_delete",
    "propose_mfa_create",
    "propose_mfa_delete",
    "propose_user_update",
    "propose_user_create",
    "propose_user_delete",
    "propose_user_custom",
    "propose_group_update",
    "propose_group_create",
    "propose_group_delete",
    "propose_server_update",
    "propose_server_group_update",
    "propose_servicepool_update",
    "propose_servicepool_create",
    "propose_servicepool_delete",
    "propose_servicepool_group_set",
    "propose_servicepool_group_add",
    "propose_servicepool_group_delete",
    "propose_servicepool_transport_set",
    "propose_servicepool_transport_add",
    "propose_servicepool_transport_delete",
    "propose_metapool_update",
    "propose_metapool_create",
    "propose_metapool_delete",
    "propose_metapool_member_set",
    "propose_metapool_group_set",
    "propose_metapool_group_add",
    "propose_metapool_group_delete",
    "propose_metapool_access_set",
    "propose_metapool_fallback_update",
    "propose_metapool_assignment_update",
    "propose_metapool_assignment_custom",
    "propose_metapool_assignment_delete",
    "propose_servicepool_access_set",
    "propose_servicepool_action_set",
    "propose_servicepool_assignable_create",
    "propose_servicepool_fallback_update",
    "propose_servicepool_publication_add",
    "propose_servicepool_publication_delete",
    "propose_servicepool_assignment_update",
    "propose_servicepool_assignment_custom",
    "propose_servicepool_assignment_delete",
    "propose_servicepool_cached_delete",
    "propose_tunnel_update",
    "propose_tunnel_create",
    "propose_tunnel_delete",
    "create_flow",
    "submit_flow",
    "list_flow_actions",
    "update_flow_action",
    "cancel_flow",
)

# Read views of the descriptor families: generated from the registry into
# the always-active curated surface (reads are not mutations).
READ_TOOL_NAMES: typing.Final[tuple[str, ...]] = (
    "get_servicepool_access",
    "get_servicepool_action",
    "get_servicepool_group",
    "get_servicepool_publication",
    "get_servicepool_transport",
    "get_servicepool_assignment",
    "get_servicepool_cached",
    "get_metapool_member",
    "get_metapool_group",
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

    def _submitted(self, flow: ActionFlow) -> ActionFlow:
        """Submit a draft flow (owner side): the admin surface begins here."""
        return self.store.submit_flow(flow, actor_uuid=self.owner.uuid)

    def _open_flow(self, **kwargs: typing.Any) -> ActionFlow:
        """A draft flow already holding one action, submitted for review."""
        flow = self._flow(**kwargs)
        self._action(flow)
        return self._submitted(flow)
