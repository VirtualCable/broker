# -*- coding: utf-8 -*-
#
# Copyright (c) 2024 Virtual Cable S.L.U.
# All rights reserved.
#
"""
Global (cross-pool) read-only user-service listings used by the dashboard KPI
drilldowns. UDS normally exposes user services only as details of a service
pool; these handlers provide flat, top-level tables so the summary cards can
link to "all user services" and "all assigned services".

Author: Adolfo Gómez / dashboard drilldowns
"""
import logging
import typing

from django.utils.translation import gettext_lazy as _

from uds import models
from uds.core import types
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

    def get_item(self, item: 'models.Model') -> UserServiceItem:
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
