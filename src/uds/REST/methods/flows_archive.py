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
Archive surface for proposal flows (``/flows/archive``).

The audit trail of the supervised mutability: decided flows whose
retention window on the approval surface is over, plus expired flows,
which arrive here immediately. Nothing on this surface can be written;
the review snapshot stays readable so an administrator can still tell
what was proposed, approved and ran. The partition logic shared with
:mod:`.flows_approval` lives in ``FlowsAdminSurface``.

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import logging
import typing

from django.db import models
from django.utils.translation import gettext_lazy as _

from uds.core import exceptions, types
from uds.core.util import ui as ui_utils
from uds.REST.model import DetailHandler

from .flows import FlowActions
from .flows_approval import FlowsAdminSurface

logger: logging.Logger = logging.getLogger(__name__)


class ArchiveFlowActions(FlowActions):
    """Read-only actions detail for the archive surface.

    The plain data and the review snapshot stay visible (the archive is
    the audit trail of what was approved and ran), but the per-action
    ``approve``/``skip`` operations are not exposed: an archived flow
    belongs to no administrator's queue anymore.
    """

    CUSTOM_METHODS: typing.ClassVar[list[types.rest.ModelCustomMethod]] = []


class FlowsArchive(FlowsAdminSurface):
    """Archive API for proposal flows (admin only, read-only).

    The complement of ``/flows/approval``: decided flows past the
    retention window and expired flows. This is the audit trail of the
    supervised mutability, so nothing here can be written: the review
    snapshot stays readable (with its actions), but the domain
    operations are not exposed, creation/edition is refused (as in
    approval) and deletion is denied outright — an archived flow is
    immutable.
    """

    PATH = "flows"
    NAME = "archive"

    _ARCHIVED_VIEW: typing.ClassVar[bool] = True

    DETAIL: typing.ClassVar[dict[str, type["DetailHandler[typing.Any]"]] | None] = {
        "actions": ArchiveFlowActions
    }

    # Read-only surface: no domain operations are exposed
    CUSTOM_METHODS: typing.ClassVar[list[types.rest.ModelCustomMethod]] = []

    FIELDS_TO_SAVE: typing.ClassVar[list[str]] = ["name", "justification"]

    TABLE = (
        ui_utils.TableBuilder(_("Archived flows"))
        .text_column(name="name", title=_("Name"))
        .text_column(name="status", title=_("Status"))
        .text_column(name="owner", title=_("Owner"))
        .numeric_column(name="actions_count", title=_("Actions"))
        .datetime_column(name="created", title=_("Created"))
        .datetime_column(name="decided_at", title=_("Decided at"))
        .with_filter_fields("name", "status", "owner")
        .build()
    )

    @typing.override
    def validate_delete(self, item: models.Model) -> None:
        raise exceptions.rest.AccessDenied("Archived flows cannot be deleted")
