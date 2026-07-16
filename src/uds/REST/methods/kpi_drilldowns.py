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
Row builders for the dashboard KPI drilldowns.

UDS exposes users, groups and user services only as details of an authenticator
or a service pool. The dashboard summary cards count them across every parent,
so their drilldowns need flat cross-parent listings. These are plain helpers:
the Authenticators and ServicesPools handlers serve them from read-only custom
methods, so no extra handler (nor menu group) is registered for them.

The columns live in the admin GUI (the tables are built client side), so these
only build the rows.

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""
import collections
import dataclasses
import typing

from django.utils.translation import gettext as _

from uds import models
from uds.core import types
from uds.core.types.states import State

from .user_services import AssignedUserService, UserServiceItem
from .users_groups import UserItem

# States that are never useful in these listings (already gone / broken)
_HIDDEN_STATES = [State.REMOVED, State.ERROR]


@dataclasses.dataclass
class GlobalUserItem(UserItem):
    # Which authenticator the user belongs to (only meaningful in a global list)
    authenticator: str = ''


@dataclasses.dataclass
class GlobalGroupItem(types.rest.BaseRestItem):
    id: str
    name: str
    comments: str
    state: str
    type: str
    authenticator: str


def _state_literal(state: str) -> str:
    """
    Translated label for a state.

    These listings are served from custom methods, so there is no server-side
    TableInfo to map raw states to labels (a model handler would do it with a
    dict_column). Same as the /servicepools custom method, the label is resolved
    here and the GUI just declares a plain text column.
    """
    return State.literals_dict().get(state, state)


def list_users_with_services() -> list[GlobalUserItem]:
    """
    Users that currently own at least one valid (usable/preparing) user service,
    across every authenticator.

    Groups are intentionally omitted to keep the query cheap (no per-user group
    lookup).
    """
    # select_related: reading user.manager.name would otherwise cost a query per row.
    qs = (
        models.User.objects.select_related('manager')
        .filter(userServices__state__in=State.VALID_STATES)
        .distinct()
    )

    return [
        GlobalUserItem(
            id=user.uuid,
            name=user.name,
            real_name=user.real_name,
            comments=user.comments,
            state=_state_literal(user.state),
            staff_member=user.staff_member,
            is_admin=user.is_admin,
            last_access=user.last_access,
            mfa_data=user.mfa_data,
            parent=user.parent,
            role=user.get_role().as_str(),
            authenticator=user.manager.name,
        )
        for user in qs
    ]


def list_groups() -> list[GlobalGroupItem]:
    """Every group/meta-group, across every authenticator."""
    # select_related: same as list_users_with_services, group.manager.name is read per row.
    return [
        GlobalGroupItem(
            id=group.uuid,
            name=group.name,
            comments=group.comments,
            state=_state_literal(group.state),
            type=str(_('Meta group') if group.is_meta else _('Group')),
            authenticator=group.manager.name,
        )
        for group in models.Group.objects.select_related('manager')
    ]


def list_user_services(*, assigned_only: bool) -> list[UserServiceItem]:
    """
    Non-removed user services, across every pool.

    Reuses the pool-scoped assigned-service serialization and adds the owning
    pool name for context. `assigned_only` drops cache entries (no owning user)
    for the "Assigned services" drilldown.
    """
    # select_related: userservice_item() walks deployed_service, publication and
    # user.manager on every row; without it each one costs an extra query.
    qs = models.UserService.objects.exclude(state__in=_HIDDEN_STATES).select_related(
        'deployed_service', 'publication', 'user__manager'
    )
    if assigned_only:  # cache entries have no owning user
        qs = qs.filter(user__isnull=False)

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
