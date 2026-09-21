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
Approval surface for proposal flows (``/flows/approval``).

The working table of the supervised mutability: everything an
administrator can still act upon, plus the recently decided flows that
linger here for the retention window before moving to the archive
(:mod:`.flows_archive`). The partition logic both administration
surfaces share lives in :class:`FlowsAdminSurface`; the owner surface
and the REST items are in :mod:`.flows`.

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import logging
import typing

from django.db import models
from django.utils.translation import gettext_lazy as _

from uds.core import consts, exceptions, types
from uds.core.types.mcp import FlowStatus
from uds.core.util import ensure
from uds.core.util import permissions
from uds.core.util import ui as ui_utils
from uds.mutability import executor
from uds.core.exceptions.mcp import InvalidTransition, StaleProposal
from uds.mutability.store import FlowStore
from uds.models import ActionFlow
from uds.REST.model import DetailHandler, ModelHandler

from .flows import FlowActions, FlowItem

logger: logging.Logger = logging.getLogger(__name__)


class FlowsAdminSurface(ModelHandler[FlowItem]):
    """Admin surfaces over proposal flows (never registered itself).

    ``/flows/approval`` and ``/flows/archive`` are a partition of the
    same admin view: every flow lives in exactly one of them. Drafts
    belong to neither (they are the owner's private composing space).
    A decided flow lingers on the approval surface for the retention
    window (GlobalConfig, MCP, "Approval Retention Days") so the
    administrator keeps the context of what they just resolved, and then
    moves to the archive. Expired flows move immediately.

    The dispatcher only registers leaf handlers (a class with
    subclasses is skipped), so the partition logic lives here and the
    concrete surfaces differ only by their declared identity and the
    operations they expose.
    """

    # Whether this surface is the archive (read-only) side of the
    # partition; set by the concrete surfaces
    _ARCHIVED_VIEW: typing.ClassVar[bool] = False

    ROLE: typing.ClassVar[consts.Role] = consts.Role.ADMIN

    MODEL = ActionFlow
    # Owners compose in drafts: they are the agent's private space until
    # submitted, so they do not exist for any administration surface.
    EXCLUDE: typing.ClassVar[dict[str, typing.Any] | None] = {"status": FlowStatus.DRAFT}

    REST_API_INFO = types.rest.api.RestApiInfo(
        typed=types.rest.api.RestApiInfoGuiType.SINGLE_TYPE,
    )

    def _belongs(self, item: ActionFlow) -> bool:
        """Is ``item`` part of this surface's side of the partition?"""
        return FlowStore.is_archived(item) == self._ARCHIVED_VIEW

    @typing.override
    def filter_model_queryset(
        self, qs: models.QuerySet[typing.Any] | None = None
    ) -> models.QuerySet[typing.Any]:
        qs = super().filter_model_queryset(qs)
        archived_q = FlowStore.archived_q()
        if self._ARCHIVED_VIEW:
            return qs.filter(archived_q)
        return qs.exclude(archived_q)

    @typing.override
    def check_access(
        self,
        obj: models.Model,
        permission: types.permissions.PermissionType,
        root: bool = False,
    ) -> None:
        """Admins reach the flows of their surface; the rest is not found.

        The generic lookup goes by uuid, so without this guard a draft
        would be readable (and its actions listable) by direct URL, and
        the approval/archive partition would leak: both cases answer the
        same opaque 404, the flow simply does not exist on this surface.
        """
        item = ensure.is_instance(obj, ActionFlow)
        if not root and (item.status == FlowStatus.DRAFT or not self._belongs(item)):
            raise exceptions.rest.NotFound("Item not found") from None
        super().check_access(obj, permission, root)

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
            decided_at=item.decided_at,
            compliance=FlowStore().flow_compliance(item),
            decided_note=item.properties.get("decided_note", ""),
        )

    @typing.override
    def pre_save(self, fields: dict[str, typing.Any]) -> None:
        raise exceptions.rest.AccessDenied("Flows cannot be created through this API")

    @typing.override
    def validate_save(self, item: models.Model) -> None:
        raise exceptions.rest.AccessDenied("Flows cannot be modified through this API")

    @typing.override
    def validate_delete(self, item: models.Model) -> None:
        """A flow disappears only through the surface that owns it.

        The generic delete looks the flow up by uuid before the
        partition is consulted, so without this guard a flow that had
        already moved to the archive (or a draft, which belongs to no
        administration surface) could be deleted from ``/flows/approval``.
        The same opaque 404 as any direct lookup on the wrong side.
        """
        flow = ensure.is_instance(item, ActionFlow)
        if flow.status == FlowStatus.DRAFT or not self._belongs(flow):
            raise exceptions.rest.NotFound("Item not found") from None


class FlowsApproval(FlowsAdminSurface):
    """Approval API for proposal flows (admin only).

    The working table of the supervised mutability: everything an
    administrator can still act upon (pending, locked, running), plus
    the decided flows within the retention window (read-only context:
    recently executed/rejected/cancelled proposals linger here before
    moving to the archive). Flows are created by their owners (normally
    through the proposal API/MCP), never through this handler:
    ``pre_save`` and ``validate_save`` refuse create/edit with a clean
    403, while read and delete remain available to administrators. The
    surface is admin-only: non-admin users get a clean 403 at the door
    (the MANAGEMENT permission governs the wrapped types on the
    proposal surface, never flow administration).
    """

    PATH = "flows"
    NAME = "approval"

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
        ui_utils.TableBuilder(_("Flows to approve"))
        .text_column(name="name", title=_("Name"))
        .text_column(name="justification", title=_("Justification"))
        .text_column(name="status", title=_("Status"))
        .text_column(name="compliance", title=_("Compliance"))
        .text_column(name="owner", title=_("Owner"))
        .numeric_column(name="actions_count", title=_("Actions"))
        .datetime_column(name="created", title=_("Created"))
        .datetime_column(name="due_date", title=_("Due date"))
        .datetime_column(name="decided_at", title=_("Decided at"))
        .with_filter_fields("name", "status", "owner", "compliance")
        .row_style(prefix="row-compliance-", field="compliance")
        .build()
    )

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
        return {"flow": flow.uuid, "status": flow.status}

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
            "status": flow.status,
            "decided_by": flow.properties.get("decided_by", ""),
            "note": flow.properties.get("decided_note", ""),
        }
