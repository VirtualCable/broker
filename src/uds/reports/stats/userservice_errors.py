# -*- coding: utf-8 -*-
#
# Copyright (c) 2026 Virtual Cable S.L.
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
import csv
import datetime
import io
import logging
import typing

from django.db.models import Count, Q
from django.utils.translation import gettext, gettext_lazy as _
from django.utils import timezone

from uds.core.types.states import State
from uds.core.ui import gui
from uds.models import ServicePool, UserService

from .base import StatsReport

logger = logging.getLogger(__name__)


class UserServiceErrorsReport(StatsReport):
    filename = 'userservice_errors.pdf'
    name = _('User services in error')
    description = _('User services that transitioned to error state per pool over a period')
    uuid = '78fe8f92-5fed-4f6b-8257-825df9d767a7'

    pools = StatsReport.pools
    start_date = StatsReport.start_date
    end_date = StatsReport.end_date

    def init_gui(self) -> None:
        vals = [gui.choice_item('0-0-0-0', gettext('ALL POOLS'))] + [
            gui.choice_item(v.uuid, v.name) for v in ServicePool.objects.all().order_by('name') if v.uuid
        ]
        self.pools.set_choices(vals)

    def get_data(self) -> tuple[list[dict[str, typing.Any]], list[dict[str, typing.Any]]]:
        start_dt = datetime.datetime.combine(self.start_date.as_date(), datetime.time.min)
        start_dt = timezone.make_aware(start_dt)
        end_dt = datetime.datetime.combine(self.end_date.as_date(), datetime.time.max)
        end_dt = timezone.make_aware(end_dt)

        if '0-0-0-0' in self.pools.value:
            pool_filter: dict[str, typing.Any] = {}
        else:
            pool_filter = {'deployed_service__uuid__in': self.pools.value}

        # state can be 'E' (ERROR) on UserService.state OR os_state.
        qs = UserService.objects.filter(
            state_date__gte=start_dt,
            state_date__lte=end_dt,
            **pool_filter,
        ).filter(Q(state=State.ERROR) | Q(os_state=State.ERROR))

        # Aggregate per pool
        agg = (
            qs.values('deployed_service__uuid', 'deployed_service__name').annotate(c=Count('id')).order_by('-c')
        )
        per_pool = [{'pool': r['deployed_service__name'], 'count': r['c']} for r in agg]

        # Detailed list (cap to a reasonable number to keep PDF small)
        detail: list[dict[str, typing.Any]] = []
        for us in qs.select_related('deployed_service', 'user').order_by('-state_date')[:1000]:
            detail.append(
                {
                    'pool': us.deployed_service.name,
                    'name': us.friendly_name,
                    'user': us.user.pretty_name if us.user else '',
                    'state': us.state,
                    'os_state': us.os_state,
                    'state_date': us.state_date,
                }
            )

        return per_pool, detail

    def generate(self) -> bytes:
        per_pool, detail = self.get_data()
        return self.template_as_pdf(
            'uds/reports/stats/userservice-errors.html',
            dct={
                'per_pool': per_pool,
                'detail': detail,
                'beginning': self.start_date.as_date(),
                'ending': self.end_date.as_date(),
            },
            header=gettext('User services in error'),
            water=gettext('UDS Report of user services in error'),
        )


class UserServiceErrorsReportCSV(UserServiceErrorsReport):
    filename = 'userservice_errors.csv'
    mime_type = 'text/csv'
    encoded = False
    uuid = 'b7797d95-548a-44bb-a061-4d1f9ad34eeb'

    pools = UserServiceErrorsReport.pools
    start_date = UserServiceErrorsReport.start_date
    end_date = UserServiceErrorsReport.end_date

    def generate(self) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                gettext('Pool'),
                gettext('User service'),
                gettext('User'),
                gettext('State'),
                gettext('OS State'),
                gettext('State date'),
            ]
        )
        _per_pool, detail = self.get_data()
        for v in detail:
            writer.writerow([v['pool'], v['name'], v['user'], v['state'], v['os_state'], v['state_date']])
        return output.getvalue().encode()
