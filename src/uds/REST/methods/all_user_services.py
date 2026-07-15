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
import collections
import typing

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from uds import models
from uds.core import types
from uds.core.types.states import State
from uds.core.util import ui as ui_utils

from .user_services import AssignedUserService, UserServiceItem

# States that are never useful in these listings (already gone / broken)
_HIDDEN_STATES = [State.REMOVED, State.ERROR]


def user_services_table() -> 'types.rest.TableInfo':
    """Columns for the flat user-service drilldowns (same shape for all/assigned)."""
    return (
        ui_utils.TableBuilder(_('User services'))
        .icon(name='friendly_name', title=_('Name'))
        .text_column(name='pool_name', title=_('Pool'))
        .text_column(name='owner', title=_('Owner'))
        .text_column(name='state', title=_('Status'))
        .text_column(name='ip', title=_('IP'))
        .datetime_column(name='creation_date', title=_('Creation date'))
        .row_style(prefix='row-state-', field='state')
    ).build()


def _base_queryset(*, assigned_only: bool) -> 'QuerySet[models.UserService]':
    # select_related: userservice_item() walks deployed_service, publication and
    # user.manager on every row; without it each one costs an extra query.
    qs = models.UserService.objects.exclude(state__in=_HIDDEN_STATES).select_related(
        'deployed_service', 'publication', 'user__manager'
    )
    if assigned_only:  # cache entries have no owning user
        qs = qs.filter(user__isnull=False)
    return qs


def list_user_services(*, assigned_only: bool) -> list[UserServiceItem]:
    """
    Flat list of user services across every pool (dashboard KPI drilldown).

    UDS normally exposes user services only as a detail of a service pool; this
    reuses that serialization and adds the owning pool name for context.
    `assigned_only` drops cache entries for the "Assigned services" drilldown.
    """
    qs = _base_queryset(assigned_only=assigned_only)

    # Properties live in the generic properties table (not a related field), so
    # userservice_item() would cost a handful of queries per row. Fetch them all
    # in one query, keyed by user-service uuid. Same approach the pool-scoped
    # assigned-services listing takes.
    props: dict[str, dict[str, typing.Any]] = collections.defaultdict(dict)
    for owner_id, key, value in models.Properties.objects.filter(
        owner_type='userservice',
        owner_id__in=qs.values_list('uuid', flat=True),
    ).values_list('owner_id', 'key', 'value'):
        props[owner_id][key] = value

    items: list[UserServiceItem] = []
    for userservice in qs:
        item = AssignedUserService.userservice_item(userservice, props.get(userservice.uuid, {}))
        item.pool_name = userservice.deployed_service.name
        items.append(item)
    return items
