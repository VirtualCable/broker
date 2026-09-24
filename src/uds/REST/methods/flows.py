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
Owner surface for proposal flows (``/flows/own``).

Every user (staff level) manages HERE only the flows it has proposed:
open a draft flow, append/edit its actions while it is still a draft,
submit it for review (pending, invisible edits from now on), inspect
them and cancel the whole flow while undecided. Approval and execution
remain on the administration surfaces (``/flows/approval`` and
``/flows/archive``), where drafts do not exist.

This module also holds the flow/action REST items and the actions
detail handler shared by all three surfaces; the administration
surfaces themselves live in :mod:`.flows_approval` and
:mod:`.flows_archive`.

The domain logic (caps, CAS bases, transitions) lives in
:mod:`uds.mutability`; this handler only exposes it over REST, so the
generic create/edit machinery is not used (staff users have no object
permissions over ``ActionFlow``; ownership is the access rule).
"""

import collections.abc
import dataclasses
import datetime
import logging
import typing

from django.db import models
from django.utils.translation import gettext_lazy as _

from uds import mutability
from uds.core import consts, exceptions, types
from uds.core.consts import mcp as consts_mcp
from uds.core.util import ensure
from uds.core.util import permissions
from uds.core.util import ui as ui_utils
from uds.core.util.model import process_uuid
from uds.mutability import registry
from uds.core.consts.mcp import CREATE_TARGET_UUID
from uds.mutability.base import ActionOperation
from uds.core.exceptions.mcp import InvalidTransition, MutabilityError, StaleProposal
from uds.mutability.store import FlowStore
from uds.models import ActionFlow, FlowAction
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
    # Touched fields as they were when the proposal was made; only for admin views
    base_values: dict[str, typing.Any] = dataclasses.field(default_factory=dict[str, typing.Any])
    # Who launched the run that executed this action, and when (the
    # launch audit frozen at EXECUTING); empty/None while not executed
    executed_by: str = ""
    executed_at: datetime.datetime | None = None


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
    # When the flow reached its terminal decision (approval-surface
    # retention reference); None while the flow is still open
    decided_at: datetime.datetime | None = None
    # Worst compliance of the actions (ok/outdated/conflict); admin views
    compliance: str = ""
    # Why the flow was decided (the reject reason, for instance); admin views
    decided_note: str = ""
    # Who launched the last execution run, and when (the launch audit,
    # frozen by the executor at run start); empty/None when never run
    executed_by: str = ""
    executed_at: datetime.datetime | None = None


class FlowActions(DetailHandler[FlowActionItem]):
    """Detail of the actions of a flow (shared base for all surfaces).

    Actions live and die with their flow: neither creation nor edition
    nor individual removal is allowed here. Subclasses decide what each
    surface exposes: the admin surfaces add the live ``compliance``
    indicator and the approval snapshot (``snap_info``), the approval
    one also the per-action ``approve``/``skip`` operations (see the
    store lifecycle), and the owner one replaces the writes with the
    proposal machinery.
    """

    # Admin-only surface: flows administration is not visible to
    # non-admin users at all (the MANAGEMENT permission governs the
    # wrapped types on the proposal surface, never this one)
    ROLE: typing.ClassVar[consts.Role] = consts.Role.ADMIN

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
        base_values: dict[str, typing.Any] = {}
        if admin_view:
            # Pre-execution drift indicator; computed live, never stored
            compliance = FlowStore().compliance(item)
            snap_info = dict(item.snap_info)
            base_values = dict(item.base_values)
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
            base_values=base_values,
            executed_by=item.executed_by,
            executed_at=item.executed_at,
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


def _ttl_from_params(params: dict[str, typing.Any]) -> datetime.timedelta | None:
    """Proposer-declared expiration window, clamped to sane bounds.

    ``expires_in_hours`` carries the expected time an administrator will
    need to resolve the proposal; out-of-range values are clamped, not
    refused (the intent — hurry up / take your time — survives).
    """
    raw = params.get("expires_in_hours")
    if raw in (None, ""):
        return None
    try:
        hours = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise exceptions.rest.RequestError("expires_in_hours must be an integer number of hours") from None
    hours = max(consts_mcp.MIN_TTL_HOURS, min(consts_mcp.MAX_TTL_HOURS, hours))
    return datetime.timedelta(hours=hours)


class FlowsOwnActions(FlowActions):
    """Owner surface for the actions of its own flows.

    Reading is inherited (ownership already enforced on the parent).
    Creating (POST) and editing (PUT, a re-base of a pending action of a
    draft flow) run through the action type registry: validation,
    MANAGEMENT permission over the target, fresh CAS base and the caps.
    Once the flow is submitted (pending) it is final: changes mean a new
    flow.

    Approving and skipping actions are admin operations (the approval
    surface only): neither the custom methods nor the admin view data
    (``snap_info`` may hold live secret field values) are exposed here.
    """

    # Not an admin view: no compliance indicator, no review snapshot
    _ADMIN_VIEW: typing.ClassVar[bool] = False
    # No admin operations on the owner surface
    CUSTOM_METHODS: typing.ClassVar[list[types.rest.ModelCustomMethod]] = []

    @staticmethod
    def _registry_type(type_id: str) -> "mutability.registry.MutableActionType":
        found = registry.get(type_id)
        if found is None:
            known = ", ".join(registry.all_type_ids())
            raise exceptions.rest.RequestError(f"Unknown action type {type_id} (known: {known})")
        action_type = found()
        return action_type

    def _require_management(
        self, action_type: "mutability.registry.MutableActionType", target_uuid: str
    ) -> typing.Any:
        target = action_type.resolve_target(target_uuid)
        # The MANAGEMENT permission is for the wrapped types (providers,
        # services, ...); configuration-like targets override the hook
        # (superuser only). See ``MutableActionType.check_propose_access``.
        action_type.check_propose_access(self._user, target)
        return target

    def _validated_payload(
        self,
        action_type: "mutability.registry.MutableActionType",
        target: typing.Any,
        *,
        justification_fallback: str = "",
    ) -> tuple[dict[str, typing.Any], dict[str, typing.Any], str, str]:
        """Validate params and build values + fresh CAS base."""
        values = self._params.get("values")
        if not isinstance(values, dict) or not values:
            # Deletion proposals carry no fields: the target itself is
            # what disappears, so an absent (or empty) payload is their
            # normal shape, not a client error.
            if action_type.operation is not ActionOperation.DELETE:
                raise exceptions.rest.RequestError("values is required and must be a non-empty object")
            values = {}
        values = typing.cast("dict[str, typing.Any]", values)
        justification = str(self._params.get("justification", "") or "") or justification_fallback
        for_type = action_type.for_type_of(target)
        errors = action_type.validate_values(for_type, values, target)
        if errors:
            raise exceptions.rest.RequestError("; ".join(errors))
        base_values, base_etag = action_type.snapshot_and_fingerprint(target, values)
        return values, base_values, base_etag, justification

    @typing.override
    def save_item(self, parent: models.Model, item: str | None) -> FlowActionItem:
        parent = ensure.is_instance(parent, ActionFlow)
        store = FlowStore()
        if item:  # Edit: re-base a pending action, keeping type and target
            action = parent.actions.get(uuid=process_uuid(item))
            action_type = self._registry_type(action.action_type)
            target = self._require_management(action_type, action.target_uuid)
            values, base_values, base_etag, justification = self._validated_payload(
                action_type, target, justification_fallback=action.justification
            )
            try:
                action = store.rebase_action(
                    action,
                    values=values,
                    base_values=base_values,
                    base_etag=base_etag,
                    justification=justification,
                )
            except InvalidTransition as e:
                raise exceptions.rest.RequestError(str(e)) from None
            return FlowActions.as_dict(action, permissions.effective_permissions(self._user, parent))

        # Create: type and target come from the request
        action_type = self._registry_type(str(self._params.get("action_type", "") or ""))
        raw_target = str(self._params.get("target_uuid", "") or "")
        justification = str(self._params.get("justification", "") or "")
        if action_type.operation is ActionOperation.CREATE:
            # Creations have no live target to ride the regular CAS path:
            # no target resolution, no base values, no fingerprint.
            create_target: models.Model | None = None
            if action_type.create_needs_parent:
                # Detail creation: the proposal targets the PARENT.
                create_target = action_type.resolve_create_parent(raw_target)
            elif raw_target not in ("", CREATE_TARGET_UUID):
                raise exceptions.rest.RequestError(
                    "A root creation takes no target_uuid: omit it (or pass the creation sentinel)"
                )
            # Create permission: MANAGEMENT over the parent for details,
            # ALL over the model type at root for root creations.
            action_type.check_create_access(self._user, create_target)
            stored_target = (
                action_type.target_uuid_of(create_target) if create_target is not None else CREATE_TARGET_UUID
            )
            values = self._params.get("values")
            if not isinstance(values, dict) or not values:
                raise exceptions.rest.RequestError("values is required and must be a non-empty object")
            values = typing.cast("dict[str, typing.Any]", values)
            errors = action_type.create_validate_values(
                action_type.create_for_type(values), values, create_target
            )
            if errors:
                raise exceptions.rest.RequestError("; ".join(errors))
            try:
                action = store.add_action(
                    parent,
                    action_type=action_type.full_id,
                    target_uuid=stored_target,
                    values=values,
                    base_values={},
                    base_etag="",
                    justification=justification,
                )
            except InvalidTransition as e:
                raise exceptions.rest.RequestError(str(e)) from None
            except MutabilityError as e:
                raise exceptions.rest.RequestError(str(e)) from None
            return FlowActions.as_dict(action, permissions.effective_permissions(self._user, parent))
        target = self._require_management(action_type, raw_target)
        values, base_values, base_etag, justification = self._validated_payload(action_type, target)
        try:
            action = store.add_action(
                parent,
                action_type=action_type.full_id,
                target_uuid=action_type.target_uuid_of(target),
                values=values,
                base_values=base_values,
                base_etag=base_etag,
                justification=justification,
            )
        except InvalidTransition as e:
            raise exceptions.rest.RequestError(str(e)) from None
        except MutabilityError as e:
            raise exceptions.rest.RequestError(str(e)) from None
        return FlowActions.as_dict(action, permissions.effective_permissions(self._user, parent))


class FlowsOwn(ModelHandler[FlowItem]):
    """Owner API for proposal flows (staff).

    Users see and manage here ONLY their own flows. The list is their
    history (drafts and decisions included), trimmed to the configured
    horizon for non-admin staff (GlobalConfig, MCP, "Own History
    Days"): recent decided flows stay, old ones quietly disappear from
    the list (housekeeping); administrators see their full history. A
    single lookup admits any status; creation opens a fresh DRAFT flow
    (caps enforced); ``submit`` closes it for review; deletion cancels a
    draft or pending flow. Mutations (actions) only apply while the flow
    is a draft.
    """

    PATH = "flows"
    NAME = "own"

    MODEL = ActionFlow
    DETAIL: typing.ClassVar[dict[str, type["DetailHandler[typing.Any]"]] | None] = {"actions": FlowsOwnActions}

    CUSTOM_METHODS: typing.ClassVar[list[types.rest.ModelCustomMethod]] = [
        types.rest.ModelCustomMethod(
            "submit",
            True,
            method=types.rest.CustomMethodMethod.POST,
            required_permission=types.permissions.PermissionType.MANAGEMENT,
            description="Close a draft flow and queue it for the administrator; from now on it cannot be edited",
        ),
    ]

    FIELDS_TO_SAVE: typing.ClassVar[list[str]] = ["name", "justification"]

    TABLE = (
        ui_utils.TableBuilder(_("My proposals"))
        .text_column(name="name", title=_("Name"))
        .text_column(name="status", title=_("Status"))
        .numeric_column(name="actions_count", title=_("Actions"))
        .datetime_column(name="created", title=_("Created"))
        .datetime_column(name="due_date", title=_("Due date"))
        .with_filter_fields("name", "status")
        .build()
    )

    @typing.override
    def check_access(
        self,
        obj: models.Model,
        permission: types.permissions.PermissionType,
        root: bool = False,
    ) -> None:
        """Ownership is the access rule: owners pass, the rest is not found.

        Root (generic create/modify paths) is never granted: creation and
        cancel are domain operations handled by this handler. Decided
        flows past the history horizon are not found either (same opaque
        404 as the list): the horizon trims the surface, not only the
        table.
        """
        item = ensure.is_instance(obj, ActionFlow)
        if root:
            raise exceptions.rest.AccessDenied("Flows are created and cancelled through the own API")
        if item.owner != self._user:
            # Opaque on purpose: do not disclose other users' flows
            raise exceptions.rest.NotFound("Item not found") from None
        if not FlowStore.visible_in_own_history(item, days=self._history_days()):
            raise exceptions.rest.NotFound("Item not found") from None

    # ------------------------------------------------------------- queries

    def _history_days(self) -> int:
        """Decided-flow horizon applied to this requester's own surface."""
        return FlowStore.own_history_days(is_admin=self._user.is_admin)

    @typing.override
    def get_items(
        self, *, sumarize: bool = False, query: models.QuerySet[typing.Any] | None = None
    ) -> collections.abc.Generator[FlowItem, None, None]:
        """Own flows: full history for admins, horizon-bound for staff."""
        days = self._history_days()
        for flow in FlowStore().list_flows(owner_uuid=self._user.uuid):
            if not FlowStore.visible_in_own_history(flow, days=days):
                continue
            yield self.get_item(flow)

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
            permission=types.permissions.PermissionType.ALL,
            decided_by=item.properties.get("decided_by", ""),
            decided_at=item.decided_at,
            executed_by=item.executed_by,
            executed_at=item.executed_at,
        )

    # ------------------------------------------------------------ mutation

    @typing.override
    def _perform_create(self) -> dict[str, typing.Any]:
        """Open a fresh draft flow owned by the requester."""
        store = FlowStore()
        try:
            flow = store.create_flow(
                owner=self._user,
                name=str(self._params.get("name", "") or ""),
                justification=str(self._params.get("justification", "") or ""),
            )
        except MutabilityError as e:
            raise exceptions.rest.RequestError(str(e)) from None
        return dict(self.get_item(flow).as_dict())

    def submit(self, item: models.Model) -> dict[str, typing.Any]:
        """Close a draft flow and queue it for the administrator.

        The proposal becomes final: actions cannot be added or edited
        afterwards, and the administrator's review window
        (``expires_in_hours``) starts counting now.
        """
        flow = ensure.is_instance(item, ActionFlow)
        try:
            flow = FlowStore().submit_flow(
                flow,
                actor_uuid=self._user.uuid,
                ttl=_ttl_from_params(self._params),
            )
        except InvalidTransition as e:
            raise exceptions.rest.RequestError(str(e)) from None
        return self.get_item(flow).as_dict()

    @typing.override
    def delete(self) -> typing.Any:
        """Cancel one of the owner's draft or pending flows."""
        if len(self._args) > 1:  # Detail deletion (actions) → refused there
            return self.process_detail()
        if len(self._args) != 1:
            raise exceptions.rest.RequestError("Delete need one and only one argument")
        flow = self._own_flow()
        store = FlowStore()
        try:
            store.cancel_flow(flow, actor_uuid=self._user.uuid)
        except InvalidTransition as e:
            raise exceptions.rest.RequestError(str(e)) from None
        return self.get_item(flow).as_dict()

    @typing.override
    def pre_save(self, fields: dict[str, typing.Any]) -> None:
        raise exceptions.rest.AccessDenied("Flows cannot be modified, only cancelled")

    @typing.override
    def validate_save(self, item: models.Model) -> None:
        raise exceptions.rest.AccessDenied("Flows cannot be modified, only cancelled")

    # ------------------------------------------------------------- helpers

    def _own_flow(self) -> ActionFlow:
        """Look up a flow of this request enforcing ownership (404 otherwise)."""
        try:
            flow = self.MODEL.objects.get(uuid__iexact=self._args[0].lower())
            self.check_access(flow, types.permissions.PermissionType.MANAGEMENT)
            flow = ensure.is_instance(flow, ActionFlow)
            # Lazy expiry so a stale flow is answered as gone, not cancellable
            return FlowStore().get_flow(flow.uuid) or flow
        except Exception as e:
            raise exceptions.rest.NotFound("Item not found") from e
