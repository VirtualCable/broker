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
Global (cross-authenticator) read-only listings used by the dashboard KPI
drilldowns. UDS normally exposes users/groups only as details of an
authenticator; these helpers build flat tables so the Authenticators handler
can serve them as custom methods (all users / all groups / users with services)
without a separate top-level endpoint and menu group.

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""
import dataclasses

from django.utils.translation import gettext_lazy as _

from uds import models
from uds.core import types
from uds.core.types.states import State
from uds.core.util import ui as ui_utils

from .users_groups import UserItem


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


def users_table() -> 'types.rest.TableInfo':
    return (
        ui_utils.TableBuilder(_('Users'))
        .icon(name='name', title=_('Username'))
        .text_column(name='authenticator', title=_('Authenticator'))
        .text_column(name='role', title=_('Role'))
        .text_column(name='real_name', title=_('Name'))
        .dict_column(
            name='state',
            title=_('Status'),
            dct={State.ACTIVE: _('Enabled'), State.INACTIVE: _('Disabled')},
        )
        .datetime_column(name='last_access', title=_('Last access'))
        .row_style(prefix='row-state-', field='state')
    ).build()


def groups_table() -> 'types.rest.TableInfo':
    return (
        ui_utils.TableBuilder(_('Groups'))
        .icon(name='name', title=_('Group'))
        .text_column(name='authenticator', title=_('Authenticator'))
        .dict_column(name='type', title=_('Type'), dct={'group': _('Group'), 'meta': _('Meta group')})
        .text_column(name='comments', title=_('Comments'))
        .dict_column(
            name='state',
            title=_('Status'),
            dct={State.ACTIVE: _('Enabled'), State.INACTIVE: _('Disabled')},
        )
        .row_style(prefix='row-state-', field='state')
    ).build()


def list_users(*, with_services_only: bool) -> list[GlobalUserItem]:
    """
    Flat list of every user across all authenticators (dashboard KPI drilldown).

    Groups are intentionally omitted to keep the query cheap (no per-user group
    lookup). `with_services_only` keeps just the users that currently own at
    least one valid (usable/preparing) user service.
    """
    # select_related: reading user.manager.name would otherwise cost a query per row.
    qs = models.User.objects.select_related('manager')
    if with_services_only:
        qs = qs.filter(userServices__state__in=State.VALID_STATES).distinct()

    return [
        GlobalUserItem(
            id=user.uuid,
            name=user.name,
            real_name=user.real_name,
            comments=user.comments,
            state=user.state,
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
    """Flat list of every group/meta-group across authenticators (KPI drilldown)."""
    # select_related: same as list_users, group.manager.name is read per row.
    return [
        GlobalGroupItem(
            id=group.uuid,
            name=group.name,
            comments=group.comments,
            state=group.state,
            type='meta' if group.is_meta else 'group',
            authenticator=group.manager.name,
        )
        for group in models.Group.objects.select_related('manager')
    ]
