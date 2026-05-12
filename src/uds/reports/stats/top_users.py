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
import collections
import csv
import io
import logging
import typing

from django.db.models import F, Window
from django.db.models.functions import Lag
from django.utils.translation import gettext, gettext_lazy as _

from uds.core.managers.stats import StatsManager
from uds.core.ui import gui
from uds.core.util import stats
from uds.models import ServicePool

from .base import StatsReport

logger = logging.getLogger(__name__)


class UserEntry(typing.TypedDict):
    sessions: int
    time: int
    pools: set[int]


class TopUsersReport(StatsReport):
    filename = 'top_users.pdf'
    name = _('Top users')
    description = _('Top users by total session time across all pools')
    uuid = '3948fd95-0117-41fe-a9ca-7c6efedd4f79'

    start_date = StatsReport.start_date
    end_date = StatsReport.end_date

    top_n = gui.NumericField(
        order=4,
        label=_('Top N'),
        length=3,
        min_value=1,
        max_value=1000,
        default=50,
        tooltip=_('Number of users to show'),
        required=True,
    )

    sort_by = gui.ChoiceField(
        order=5,
        label=_('Sort by'),
        tooltip=_('Sorting criterion'),
        default='time',
        choices=[
            gui.choice_item('time', _('Total time')),
            gui.choice_item('sessions', _('Sessions')),
        ],
    )

    def get_data(self) -> list[dict[str, typing.Any]]:
        start = self.start_date.as_timestamp()
        end = self.end_date.as_timestamp()

        login = stats.events.types.stats.EventType.LOGIN
        logout = stats.events.types.stats.EventType.LOGOUT

        partition = [F('owner_id'), F('fld4')]
        items = (
            StatsManager.manager()
            .enumerate_events(
                stats.events.types.stats.EventOwnerType.SERVICEPOOL,
                (login, logout),
                owner_id=list(ServicePool.objects.values_list('id', flat=True)),
                since=start,
                to=end,
            )
            .annotate(
                prev_type=Window(Lag('event_type'), partition_by=partition, order_by=[F('stamp')]),
                prev_stamp=Window(Lag('stamp'), partition_by=partition, order_by=[F('stamp')]),
            )
            .values('event_type', 'stamp', 'fld4', 'prev_type', 'prev_stamp', 'owner_id')
        )

        users: dict[str, UserEntry] = collections.defaultdict(
            lambda: UserEntry(sessions=0, time=0, pools=set())
        )
        for i in items:
            if i['event_type'] != logout or i['prev_type'] != login:
                continue
            entry = users[i['fld4'] or '']
            entry['sessions'] += 1
            entry['time'] += i['stamp'] - i['prev_stamp']
            entry['pools'].add(i['owner_id'])

        rows = [
            {
                'user': k,
                'sessions': v['sessions'],
                'pools': len(v['pools']),
                'time_seconds': v['time'],
                'hours': '{:.2f}'.format(v['time'] / 3600.0),
                'average': '{:.2f}'.format(v['time'] / 3600.0 / v['sessions']),
            }
            for k, v in users.items()
        ]

        if self.sort_by.value == 'sessions':
            rows.sort(key=lambda r: r['sessions'], reverse=True)
        else:
            rows.sort(key=lambda r: r['time_seconds'], reverse=True)

        return rows[: self.top_n.as_int()]

    def generate(self) -> bytes:
        items = self.get_data()
        return self.template_as_pdf(
            'uds/reports/stats/top-users.html',
            dct={
                'data': items,
                'beginning': self.start_date.as_date(),
                'ending': self.end_date.as_date(),
                'top_n': self.top_n.as_int(),
            },
            header=gettext('Top users by usage'),
            water=gettext('UDS Top users report'),
        )


class TopUsersReportCSV(TopUsersReport):
    filename = 'top_users.csv'
    mime_type = 'text/csv'
    encoded = False
    uuid = 'eb31b347-0f68-4509-b807-988815ae53ee'

    start_date = TopUsersReport.start_date
    end_date = TopUsersReport.end_date
    top_n = TopUsersReport.top_n
    sort_by = TopUsersReport.sort_by

    def generate(self) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                gettext('User'),
                gettext('Sessions'),
                gettext('Pools used'),
                gettext('Hours'),
                gettext('Average hours/session'),
            ]
        )
        for v in self.get_data():
            writer.writerow([v['user'], v['sessions'], v['pools'], v['hours'], v['average']])
        return output.getvalue().encode()
