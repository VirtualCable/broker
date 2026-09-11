#
# Copyright (c) 2025 Virtual Cable S.L.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright notice,
#      this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright notice,
#      this list of conditions and the following disclaimer in the documentation
#      and/or other materials provided with the distribution.
#    * Neither the name of Virtual Cable S.L. nor the names of its contributors
#      may be used to endorse or promote products derived from this software
#      without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""
Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import dataclasses
import datetime
import logging
import typing

from django.db import models
from django.utils.translation import gettext_lazy as _

from uds.core import exceptions, types
from uds.core.util import ensure
from uds.core.util import permissions
from uds.core.util import ui as ui_utils
from uds.core.util.model import process_uuid

from uds.models import ActionFlow, FlowAction
from uds.mutability import executor
from uds.mutability.store import FlowStore, InvalidTransition, StaleProposal
from uds.REST.model import DetailHandler, ModelHandler

logger: logging.Logger = logging.getLogger(__name__)


@dataclasses.dataclass
class FlowActionItem(types.rest.BaseRestItem):
    id: str
    order: int
    action_type: str
    target_kind: str
    target_uuid: str
    justification: str
    status: str
    values: dict[str, typing.Any]
    created: datetime.datetime
    permission: int
    result: str | None
    # Live drift indicator (ok/outdated/conflict); only for admin views
    compliance: str = ""
    # Display snapshot frozen at approval time; only for admin views
    snap_info: dict[str, typing.Any] = dataclasses.field(default_factory=dict[str, typing.Any])


class FlowActions(DetailHandler[FlowActionItem]):
    """Detail of the actions of a flow (management surface).

    Actions live and die with their flow: neither creation nor edition
    nor individual removal is allowed here. The admin gets, besides the
    plain data, the live ``compliance`` indicator and the approval
    snapshot (``snap_info``), plus the per-action ``approve``/``skip``
    operations (see the store lifecycle).
    """

    # Admin surface: expose compliance and the approval snapshot
    _ADMIN_VIEW: typing.ClassVar[bool] = True

    CUSTOM_METHODS: typing.ClassVar[list[types.rest.ModelCustomMethod]] = [
        types.rest.ModelCustomMethod(
            "approve",
            True,
            method=types.rest.CustomMethodMethod.POST,
            required_permission=types.permissions.PermissionType.MANAGEMENT,
            description="Approve an action, freezing its live CAS state (also re-approves failed/revoked actions)",
        ),
        types.rest.ModelCustomMethod(
            "skip",
            True,
            method=types.rest.CustomMethodMethod.POST,
            required_permission=types.permissions.PermissionType.MANAGEMENT,
            description="Skip an action: it will not run when the flow is launched",
        ),
    ]

    @staticmethod
    def as_dict(item: "FlowAction", perm: int, *, admin_view: bool = False) -> FlowActionItem:
        compliance = ""
        snap_info: dict[str, typing.Any] = {}
        if admin_view:
            # Pre-execution drift indicator; computed live, never stored
            compliance = FlowStore().compliance(item)
            snap_info = dict(item.snap_info)
        return FlowActionItem(
            id=item.uuid,
            order=item.order,
            action_type=item.action_type,
            target_kind=item.target_kind,
            target_uuid=item.target_uuid,
            justification=item.justification,
            status=item.status,
            values=item.values or {},
            created=item.created,
            permission=perm,
            result=item.properties.get("result"),
            compliance=compliance,
            snap_info=snap_info,
        )

    @typing.override
    def get_item_position(self, parent: models.Model, item_uuid: str) -> int:
        parent = ensure.is_instance(parent, ActionFlow)
        return self.calc_item_position(item_uuid, parent.actions.order_by("order"))

    @typing.override
    def get_items(self, parent: models.Model) -> types.rest.ItemsResult[FlowActionItem]:
        parent = ensure.is_instance(parent, ActionFlow)
        perm = permissions.effective_permissions(self._user, parent)
        return [
            FlowActions.as_dict(action, perm, admin_view=self._ADMIN_VIEW)
            for action in self.odata_filter(parent.actions.order_by("order"))
        ]

    @typing.override
    def get_item(self, parent: models.Model, item: str) -> FlowActionItem:
        parent = ensure.is_instance(parent, ActionFlow)
        action = parent.actions.get(uuid=process_uuid(item))
        return FlowActions.as_dict(
            action, permissions.effective_permissions(self._user, parent), admin_view=self._ADMIN_VIEW
        )

    @typing.override
    def get_table(self, parent: models.Model) -> types.rest.TableInfo:
        parent = ensure.is_instance(parent, ActionFlow)
        return (
            ui_utils.TableBuilder(_("Actions of {0}").format(parent.name))
            .numeric_column(name="order", title=_("Order"))
            .text_column(name="action_type", title=_("Action"))
            .text_column(name="target_kind", title=_("Target kind"))
            .text_column(name="status", title=_("Status"))
            .text_column(name="compliance", title=_("Compliance"))
            .datetime_column(name="created", title=_("Created"))
            .with_filter_fields("action_type", "target_kind", "status", "compliance")
            .build()
        )

    @typing.override
    def save_item(self, parent: models.Model, item: str | None) -> FlowActionItem:
        raise exceptions.rest.RequestError("Flow actions cannot be created or edited")

    @typing.override
    def delete_item(self, parent: models.Model, item: str) -> None:
        raise exceptions.rest.RequestError("Flow actions cannot be deleted individually")

    # ------------------------------------------------------ domain actions

    def approve(self, parent: models.Model, item: str) -> FlowActionItem:
        """Approve an action, freezing its live CAS state.

        The approval snapshot (``snap_info``) and the frozen CAS
        reference are taken in the same step; failed/revoked actions can
        be approved again (recovery). Approving a pending flow acquires
        it (LOCKED).
        """
        parent = ensure.is_instance(parent, ActionFlow)
        action = parent.actions.get(uuid=process_uuid(item))
        try:
            action = FlowStore().approve_action(action, admin=self._user)
        except (InvalidTransition, StaleProposal) as e:
            raise exceptions.rest.RequestError(str(e)) from None
        return FlowActions.as_dict(
            action, permissions.effective_permissions(self._user, parent), admin_view=True
        )

    def skip(self, parent: models.Model, item: str) -> FlowActionItem:
        """Skip an action: it will not run when the flow is launched."""
        parent = ensure.is_instance(parent, ActionFlow)
        action = parent.actions.get(uuid=process_uuid(item))
        try:
            action = FlowStore().skip_action(action, admin=self._user)
        except (InvalidTransition, StaleProposal) as e:
            raise exceptions.rest.RequestError(str(e)) from None
        return FlowActions.as_dict(
            action, permissions.effective_permissions(self._user, parent), admin_view=True
        )


@dataclasses.dataclass
class FlowItem(types.rest.BaseRestItem):
    id: str
    name: str
    justification: str
    status: str
    owner: str
    approved_by: str
    approved_at: datetime.datetime | None
    due_date: datetime.datetime | None
    created: datetime.datetime
    actions_count: int
    permission: int
    decided_by: str
    # Worst compliance of the actions (ok/outdated/conflict); admin views
    compliance: str = ""


class FlowsManagement(ModelHandler[FlowItem]):
    """Management API for proposal flows (admin only).

    Flows are created by their owners (normally through the proposal
    API/MCP), never through this handler: ``pre_save`` and
    ``validate_save`` refuse create/edit with a clean 403, while read
    and delete remain available to administrators (staff only sees
    flows it has been granted permission over).
    """

    PATH = "flows"
    NAME = "management"

    MODEL = ActionFlow
    DETAIL: typing.ClassVar[dict[str, type["DetailHandler[typing.Any]"]] | None] = {"actions": FlowActions}

    CUSTOM_METHODS: typing.ClassVar[list[types.rest.ModelCustomMethod]] = [
        types.rest.ModelCustomMethod(
            "lock",
            True,
            method=types.rest.CustomMethodMethod.POST,
            required_permission=types.permissions.PermissionType.MANAGEMENT,
            description="Acquire a pending flow for review: it leaves the agent's view and its owner can no longer edit it",
        ),
        types.rest.ModelCustomMethod(
            "approve",
            True,
            method=types.rest.CustomMethodMethod.POST,
            required_permission=types.permissions.PermissionType.MANAGEMENT,
            description="Verify (CAS, whole item) and approve a pending or locked flow, then execute it; drifted actions are revoked and the launch is refused",
        ),
        types.rest.ModelCustomMethod(
            "reject",
            True,
            method=types.rest.CustomMethodMethod.POST,
            required_permission=types.permissions.PermissionType.MANAGEMENT,
            description="Reject a pending or locked flow; its undecided actions are skipped",
        ),
    ]

    FIELDS_TO_SAVE: typing.ClassVar[list[str]] = ["name", "justification"]

    TABLE = (
        ui_utils.TableBuilder(_("Flows"))
        .text_column(name="name", title=_("Name"))
        .text_column(name="status", title=_("Status"))
        .text_column(name="owner", title=_("Owner"))
        .numeric_column(name="actions_count", title=_("Actions"))
        .datetime_column(name="created", title=_("Created"))
        .datetime_column(name="due_date", title=_("Due date"))
        .with_filter_fields("name", "status", "owner")
        .build()
    )

    REST_API_INFO = types.rest.api.RestApiInfo(
        typed=types.rest.api.RestApiInfoGuiType.SINGLE_TYPE,
    )

    @typing.override
    def get_item(self, item: models.Model) -> FlowItem:
        item = ensure.is_instance(item, ActionFlow)
        return FlowItem(
            id=item.uuid,
            name=item.name,
            justification=item.justification,
            status=item.status,
            owner=item.owner_display,
            approved_by=item.approved_by.name if item.approved_by else "",
            approved_at=item.approved_at,
            due_date=item.due_date,
            created=item.created,
            actions_count=item.actions.count(),
            permission=permissions.effective_permissions(self._user, item),
            decided_by=item.properties.get("decided_by", ""),
            compliance=FlowStore().flow_compliance(item),
        )

    @typing.override
    def pre_save(self, fields: dict[str, typing.Any]) -> None:
        raise exceptions.rest.AccessDenied("Flows cannot be created through this API")

    @typing.override
    def validate_save(self, item: models.Model) -> None:
        raise exceptions.rest.AccessDenied("Flows cannot be modified through this API")

    # ------------------------------------------------------ domain actions

    def lock(self, item: models.Model) -> dict[str, typing.Any]:
        """Acquire a pending flow for review.

        The flow leaves the agent's visibility and its owner can no
        longer edit (nor cancel) it: the review happens on stable data.
        Locking an already locked flow is a no-op.
        """
        flow = ensure.is_instance(item, ActionFlow)
        try:
            FlowStore().lock_flow(flow, admin=self._user)
        except InvalidTransition as e:
            raise exceptions.rest.RequestError(str(e)) from None
        flow.refresh_from_db()
        return {"flow": flow.uuid, "status": str(flow.status)}

    def approve(self, item: models.Model) -> dict[str, typing.Any]:
        """Verify (pass 1) and approve a pending or locked flow, then execute it.

        Every action must be approved or skipped (at least one
        approved). Each approved action is re-verified against the CAS
        state frozen when the administrator approved it — the whole
        item: actions whose target changed at all are revoked, the flow
        stays as it was and the launch is refused, so the administrator
        can re-approve or skip them. The response carries the execution
        summary once the flow is launched; a mid-run failure returns
        the flow to LOCKED (recoverable).
        """
        flow = ensure.is_instance(item, ActionFlow)
        store = FlowStore()
        try:
            revoked = store.verify_flow(flow)
            if revoked:
                detail = ", ".join(f"{a.order} ({a.uuid})" for a in revoked)
                raise exceptions.rest.RequestError(
                    "Launch refused: actions "
                    f"{detail} revoked because their target changed after they were approved; "
                    "re-approve or skip them and launch again"
                )
            store.approve_flow(flow, admin=self._user)
        except (InvalidTransition, StaleProposal) as e:
            raise exceptions.rest.RequestError(str(e)) from None
        return executor.execute_flow(flow, self._request)

    def reject(self, item: models.Model) -> dict[str, typing.Any]:
        """Reject a pending flow; its pending actions are skipped."""
        flow = ensure.is_instance(item, ActionFlow)
        try:
            FlowStore().reject_flow(
                flow, admin=self._user.name, reason=str(self._params.get("reason", "") or "") or None
            )
        except InvalidTransition as e:
            raise exceptions.rest.RequestError(str(e)) from None
        flow.refresh_from_db()
        return {
            "flow": flow.uuid,
            "status": str(flow.status),
            "decided_by": flow.properties.get("decided_by", ""),
            "note": flow.properties.get("decided_note", ""),
        }
