# -*- coding: utf-8 -*-
#
# Copyright (c) 2026 Virtual Cable S.L.U.
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
Global (cross-pool) read-only user-service listings used by the dashboard KPI
drilldowns. UDS normally exposes user services only as details of a service
pool; these handlers provide flat, top-level tables so the summary cards can
link to "all user services" and "all assigned services".

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""
import logging
import typing

from django.db.models import Model
from django.utils.translation import gettext_lazy as _

from uds import models
from uds.core.types.states import State
from uds.core.util import ui as ui_utils

from .all_users import _ReadOnlyModelHandler
from .user_services import AssignedUserService, UserServiceItem

logger = logging.getLogger(__name__)

# States that are never useful in these listings (already gone / broken)
_HIDDEN_STATES = [State.REMOVED, State.ERROR]


class _AllUserServicesMaster(_ReadOnlyModelHandler[UserServiceItem]):
    """
    Not registered (has subclasses). Flat list of user services across every
    pool, excluding removed/errored ones. Reuses the assigned-service
    serialization and adds the owning pool name for context.
    """

    MODEL = models.UserService

    TABLE = (
        ui_utils.TableBuilder(_('User services'))
        .icon(name='friendly_name', title=_('Name'))
        .text_column(name='pool_name', title=_('Pool'))
        .text_column(name='owner', title=_('Owner'))
        .text_column(name='state', title=_('Status'))
        .text_column(name='ip', title=_('IP'))
        .datetime_column(name='creation_date', title=_('Creation date'))
        .row_style(prefix='row-state-', field='state')
    ).build()

    def filter_model_queryset(self, qs: typing.Any = None) -> typing.Any:
        qs = super().filter_model_queryset(qs)
        return qs.exclude(state__in=_HIDDEN_STATES)

    def get_item(self, item: 'Model') -> UserServiceItem:
        userservice = typing.cast('models.UserService', item)
        rest_item = AssignedUserService.userservice_item(userservice)
        rest_item.pool_name = userservice.deployed_service.name
        return rest_item


class AllUserServices(_AllUserServicesMaster):
    """Registered as /alluserservices: every non-removed user service."""


class AllAssignedServices(_AllUserServicesMaster):
    """
    Registered as /allassignedservices: only user services assigned to a user
    (cache entries excluded). Backs the "Assigned services" KPI drilldown.
    """

    def filter_model_queryset(self, qs: typing.Any = None) -> typing.Any:
        qs = super().filter_model_queryset(qs)
        return qs.filter(user__isnull=False)
