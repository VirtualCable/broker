#
# Copyright (c) 2012-2023 Virtual Cable S.L.
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

import logging
import typing

from django.db import models

from uds.core.types.mcp import FlowActionStatus, FlowStatus
from uds.core.util import properties
from uds.core.util.model import sql_now

from .user import User
from .uuid_model import UUIDModel

logger: logging.Logger = logging.getLogger(__name__)

if typing.TYPE_CHECKING:
    from django.db.models.manager import RelatedManager


class ActionFlow(UUIDModel, properties.PropertiesMixin):
    """
    A proposed sequence of changes, created by an user (normally an AI agent
    acting on their behalf) and subject to admin approval before execution.

    Actions are ordered (see FlowAction.order); execution runs them in strict
    sequence, stopping on first failure.

    Auxiliary data (as the cached owner name) is kept on Properties, not
    as columns.
    """

    name = models.CharField(max_length=128, default="")
    justification = models.TextField(default="")
    # Callable choices (django-stubs does not know them yet): the migration
    # stores the reference, so enum changes never generate migrations again
    status = models.CharField(
        max_length=16,
        choices=typing.cast(typing.Any, FlowStatus.as_choices),
        default=FlowStatus.PENDING,
        db_index=True,
    )

    # SET_NULL keeps the flow as audit trail even if the user is removed
    owner = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="action_flows"
    )

    approved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="approved_action_flows"
    )
    approved_at = models.DateTimeField(null=True, blank=True, default=None)

    # Pending flows past this date are expired (set by the application layer on creation)
    due_date = models.DateTimeField(null=True, blank=True, default=None, db_index=True)

    created = models.DateTimeField(default=sql_now, blank=True)

    # "fake" declarations for type checking
    actions: "RelatedManager[FlowAction]"

    class Meta:  # pyright: ignore
        """
        Meta class to declare db table
        """

        db_table = "uds_action_flow"
        app_label = "uds"

    @typing.override
    def get_owner_id_and_type(self) -> tuple[str, str]:
        return self.uuid, "actionflow"

    @property
    def owner_display(self) -> str:
        """
        Returns the owner name, falling back to the cached value on
        properties if the user no longer exists.
        """
        if self.owner:
            return self.owner.name
        return str(self.properties.get("owner_name", ""))

    @typing.override
    def __str__(self) -> str:
        return f"ActionFlow: {self.uuid} {self.name} ({self.status})"


class FlowAction(UUIDModel, properties.PropertiesMixin):
    """
    A single proposed change inside an ActionFlow, executed in ``order``.

    ``values`` is the proposed payload (same shape as the equivalent REST
    put). All the dynamic data — the CAS snapshots (``base_values``,
    ``base_etag`` taken at proposal time, ``approved_etag``/
    ``approved_values`` frozen at approval time) and the approval display
    cache (``snap_info``) — lives on Properties: free, schemaless, and
    kept out of the row.
    """

    flow = models.ForeignKey(ActionFlow, on_delete=models.CASCADE, related_name="actions")
    order = models.PositiveIntegerField(default=0, db_index=True)

    action_type = models.CharField(max_length=64, default="")
    target_kind = models.CharField(max_length=32, default="", db_index=True)
    target_uuid = models.CharField(max_length=50, default="", db_index=True)

    justification = models.TextField(default="")

    values: typing.Any = models.JSONField(null=True, blank=True, default=None)

    status = models.CharField(
        max_length=16,
        choices=typing.cast("typing.Any", FlowActionStatus.as_choices),
        default=FlowActionStatus.PENDING,
        db_index=True,
    )

    created = models.DateTimeField(default=sql_now, blank=True)

    class Meta:  # pyright: ignore
        """
        Meta class to declare db table
        """

        db_table = "uds_flow_action"
        app_label = "uds"
        constraints: typing.ClassVar[list[models.UniqueConstraint]] = [
            models.UniqueConstraint(fields=["flow", "order"], name="u_flwact_flow_order")
        ]

    @typing.override
    def get_owner_id_and_type(self) -> tuple[str, str]:
        return self.uuid, "flowaction"

    @property
    def base_values(self) -> dict[str, typing.Any]:
        """CAS snapshot of the touched fields, taken at proposal time."""
        return typing.cast("dict[str, typing.Any]", self.properties.get("base_values", {}))

    @base_values.setter
    def base_values(self, value: dict[str, typing.Any]) -> None:
        self.properties["base_values"] = value

    @property
    def base_etag(self) -> str:
        """Whole-item fingerprint of the target at proposal time."""
        return str(self.properties.get("base_etag", ""))

    @base_etag.setter
    def base_etag(self, value: str) -> None:
        self.properties["base_etag"] = value

    @property
    def approved_etag(self) -> str:
        """Whole-item fingerprint frozen when the action was approved.

        Empty when the action has not been approved yet.
        """
        return str(self.properties.get("approved_etag", ""))

    @approved_etag.setter
    def approved_etag(self, value: str) -> None:
        self.properties["approved_etag"] = value

    @property
    def approved_values(self) -> dict[str, typing.Any]:
        """Live values of the touched fields, frozen at approval time.

        CAS reference for the compliance indicator, alongside
        ``approved_etag``; empty until approved, cleared when the action
        is skipped.
        """
        return typing.cast("dict[str, typing.Any]", self.properties.get("approved_values", {}))

    @approved_values.setter
    def approved_values(self, value: dict[str, typing.Any]) -> None:
        self.properties["approved_values"] = value

    @property
    def snap_info(self) -> dict[str, typing.Any]:
        """
        Display data for the admin diff (target name, current values at
        approval time, field definitions, ...), stored on Properties.
        """
        return typing.cast("dict[str, typing.Any]", self.properties.get("snap_info", {}))

    @snap_info.setter
    def snap_info(self, value: dict[str, typing.Any]) -> None:
        self.properties["snap_info"] = value

    @typing.override
    def __str__(self) -> str:
        return f"FlowAction: {self.uuid} #{self.order} {self.action_type} on {self.target_uuid} ({self.status})"


properties.PropertiesMixin.setup_signals(ActionFlow)
properties.PropertiesMixin.setup_signals(FlowAction)
