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
create a flow, append/edit its actions while pending, inspect them and
cancel the whole flow. Approval and execution remain on the management
surface (``/flows/management``).

The domain logic (caps, CAS bases, transitions) lives in
:mod:`uds.mutability`; this handler only exposes it over REST, so the
generic create/edit machinery is not used (staff users have no object
permissions over ``ActionFlow``; ownership is the access rule).
"""

import collections.abc
import datetime
import logging
import typing

from django.db import models
from django.utils.translation import gettext_lazy as _

from uds import mutability
from uds.core import exceptions, types
from uds.core.consts import mcp as consts_mcp
from uds.core.types.mcp import FlowStatus
from uds.core.util import ensure
from uds.core.util import permissions
from uds.core.util import ui as ui_utils
from uds.core.util.model import process_uuid
from uds.mutability import registry
from uds.mutability.store import FlowStore, InvalidTransition, MutabilityError
from uds.models import ActionFlow
from uds.REST.model import DetailHandler, ModelHandler

from .flows_management import FlowActionItem, FlowActions, FlowItem

logger: logging.Logger = logging.getLogger(__name__)


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
    Creating (POST) and editing (PUT, a re-base of a pending action)
    run through the action type registry: validation, MANAGEMENT
    permission over the target, fresh CAS base and the caps.
    """

    @staticmethod
    def _registry_type(type_id: str) -> "mutability.registry.MutableActionType":
        action_type = registry.get(type_id)
        if action_type is None:
            known = ", ".join(t.type_id for t in registry.all_types())
            raise exceptions.rest.RequestError(f"Unknown action type {type_id} (known: {known})")
        return action_type

    def _require_management(
        self, action_type: "mutability.registry.MutableActionType", target_uuid: str
    ) -> typing.Any:
        target = action_type.resolve_target(target_uuid)
        if not permissions.has_access(self._user, target, types.permissions.PermissionType.MANAGEMENT):
            raise exceptions.rest.AccessDenied()
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
            raise exceptions.rest.RequestError("values is required and must be a non-empty object")
        values = typing.cast("dict[str, typing.Any]", values)
        justification = str(self._params.get("justification", "") or "") or justification_fallback
        for_type = action_type.for_type_of(target)
        errors = action_type.validate_values(for_type, values)
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
            if parent.status == FlowStatus.EXPIRED:
                # Revival: within the grace window, editing an expired
                # proposal brings the whole flow back to pending
                store.reopen_flow(
                    parent,
                    actor_uuid=str(self._user.uuid),
                    ttl=_ttl_from_params(self._params) or datetime.timedelta(days=consts_mcp.FLOW_TTL_DAYS),
                )
                action.refresh_from_db()
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
        target = self._require_management(action_type, str(self._params.get("target_uuid", "") or ""))
        values, base_values, base_etag, justification = self._validated_payload(action_type, target)
        try:
            action = store.add_action(
                parent,
                action_type=action_type.type_id,
                target_uuid=target.uuid,
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

    Users see and manage here ONLY their own flows. The list is the full
    history (decisions included); a single lookup admits any status;
    creation appends a fresh pending flow (caps enforced); deletion
    cancels a pending flow. Mutations only apply while pending.
    """

    PATH = "flows"
    NAME = "own"

    MODEL = ActionFlow
    DETAIL: typing.ClassVar[dict[str, type["DetailHandler[typing.Any]"]] | None] = {"actions": FlowsOwnActions}

    CUSTOM_METHODS: typing.ClassVar[list[types.rest.ModelCustomMethod]] = []

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
        cancel are domain operations handled by this handler.
        """
        item = ensure.is_instance(obj, ActionFlow)
        if root:
            raise exceptions.rest.AccessDenied("Flows are created and cancelled through the own API")
        if item.owner != self._user:
            # Opaque on purpose: do not disclose other users' flows
            raise exceptions.rest.NotFound("Item not found") from None

    # ------------------------------------------------------------- queries

    @typing.override
    def get_items(
        self, *, sumarize: bool = False, query: models.QuerySet[typing.Any] | None = None
    ) -> collections.abc.Generator[FlowItem, None, None]:
        """All own flows, decided ones included (full history for the owner)."""
        for flow in FlowStore().list_flows(owner_uuid=self._user.uuid):
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
        )

    # ------------------------------------------------------------ mutation

    @typing.override
    def _perform_create(self) -> dict[str, typing.Any]:
        """Create a fresh pending flow owned by the requester."""
        store = FlowStore()
        try:
            flow = store.create_flow(
                owner=self._user,
                name=str(self._params.get("name", "") or ""),
                justification=str(self._params.get("justification", "") or ""),
                ttl=_ttl_from_params(self._params),
            )
        except MutabilityError as e:
            raise exceptions.rest.RequestError(str(e)) from None
        return dict(self.get_item(flow).as_dict())

    @typing.override
    def delete(self) -> typing.Any:
        """Cancel one of the owner's pending flows."""
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
