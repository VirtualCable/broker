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
authenticator; these handlers provide flat, top-level tables so the summary
cards can link to "all users" / "all groups" / "users with services".

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""
import dataclasses
import logging
import typing

from django.db.models import Model
from django.utils.translation import gettext_lazy as _

from uds import models
from uds.core import exceptions, types
from uds.core.types.rest import T_Item
from uds.core.types.states import State
from uds.core.util import ui as ui_utils

from ..model import ModelHandler
from .users_groups import UserItem

logger = logging.getLogger(__name__)


# Note: T_Item must be referenced by plain name (not `types.rest.T_Item`), or the
# type checkers do not see this class as generic and every get_item() override below
# is reported as an inconsistent override.
class _ReadOnlyModelHandler(ModelHandler[T_Item]):
    """
    Base for read-only global listings: writes are rejected so these
    dashboard drilldown endpoints never mutate data.
    """

    def put(self) -> typing.Any:
        raise exceptions.rest.NotSupportedError(_('This endpoint is read-only'))

    def post(self) -> typing.Any:
        raise exceptions.rest.NotSupportedError(_('This endpoint is read-only'))

    def delete(self) -> typing.Any:
        raise exceptions.rest.NotSupportedError(_('This endpoint is read-only'))


@dataclasses.dataclass
class GlobalUserItem(UserItem):
    # Which authenticator the user belongs to (only meaningful in a global list)
    authenticator: str = ''


class _AllUsersMaster(_ReadOnlyModelHandler[GlobalUserItem]):
    """
    Not registered (has subclasses). Flat list of every user across all
    authenticators. Groups are intentionally omitted to keep the list query
    cheap (no per-user group lookup).
    """

    MODEL = models.User

    TABLE = (
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

    def get_item(self, item: 'Model') -> GlobalUserItem:
        user = typing.cast('models.User', item)
        return GlobalUserItem(
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


class AllUsers(_AllUsersMaster):
    """Registered as /allusers: every user across all authenticators."""


class UsersWithServices(_AllUsersMaster):
    """
    Registered as /userswithservices: only users that currently own at least
    one valid (usable/preparing) user service. Backs the "Users with services"
    KPI drilldown.
    """

    def filter_model_queryset(self, qs: typing.Any = None) -> typing.Any:
        qs = super().filter_model_queryset(qs)
        return qs.filter(userServices__state__in=State.VALID_STATES).distinct()


@dataclasses.dataclass
class GlobalGroupItem(types.rest.BaseRestItem):
    id: str
    name: str
    comments: str
    state: str
    type: str
    authenticator: str


class AllGroups(_ReadOnlyModelHandler[GlobalGroupItem]):
    """Registered as /allgroups: every group/meta-group across authenticators."""

    MODEL = models.Group

    TABLE = (
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

    def get_item(self, item: 'Model') -> GlobalGroupItem:
        group = typing.cast('models.Group', item)
        return GlobalGroupItem(
            id=group.uuid,
            name=group.name,
            comments=group.comments,
            state=group.state,
            type='meta' if group.is_meta else 'group',
            authenticator=group.manager.name,
        )
