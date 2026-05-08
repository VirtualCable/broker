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

from django.db.models import F, Window
from django.db.models.functions import Lag
from django.utils.translation import gettext, gettext_lazy as _
from django.utils import timezone

from uds.core.managers.stats import StatsManager
from uds.core.ui import gui
from uds.core.util import stats
from uds.models import ServicePool

from .base import StatsReport

logger = logging.getLogger(__name__)


class PeakConcurrencyReport(StatsReport):
    filename = 'peak_concurrency.pdf'
    name = _('Peak concurrent sessions')
    description = _('Peak number of concurrent user sessions per pool over a period')
    uuid = 'ca1a7f6d-a4f6-43e4-abc6-43692ba40190'

    pools = StatsReport.pools
    start_date = StatsReport.start_date
    end_date = StatsReport.end_date

    def init_gui(self) -> None:
        vals = [gui.choice_item('0-0-0-0', gettext('ALL POOLS'))] + [
            gui.choice_item(v.uuid, v.name) for v in ServicePool.objects.all().order_by('name') if v.uuid
        ]
        self.pools.set_choices(vals)

    def get_data(self) -> list[dict[str, typing.Any]]:
        start = self.start_date.as_timestamp()
        end = self.end_date.as_timestamp()

        if '0-0-0-0' in self.pools.value:
            pools = ServicePool.objects.all()
        else:
            pools = ServicePool.objects.filter(uuid__in=self.pools.value)

        pool_map: dict[int, ServicePool] = {p.id: p for p in pools}
        if not pool_map:
            return []

        login = stats.events.types.stats.EventType.LOGIN
        logout = stats.events.types.stats.EventType.LOGOUT

        partition = [F('owner_id'), F('fld4')]
        items = (
            StatsManager.manager()
            .enumerate_events(
                stats.events.types.stats.EventOwnerType.SERVICEPOOL,
                (login, logout),
                owner_id=list(pool_map.keys()),
                since=start,
                to=end,
            )
            .annotate(
                prev_type=Window(Lag('event_type'), partition_by=partition, order_by=[F('stamp')]),
                prev_stamp=Window(Lag('stamp'), partition_by=partition, order_by=[F('stamp')]),
            )
            .values('owner_id', 'event_type', 'stamp', 'prev_type', 'prev_stamp')
        )

        # Build delta events per pool: +1 at login, -1 at logout (paired only).
        deltas: dict[int, list[tuple[int, int]]] = {pid: [] for pid in pool_map}
        for i in items:
            if i['event_type'] != logout or i['prev_type'] != login:
                continue
            deltas[i['owner_id']].append((i['prev_stamp'], 1))
            deltas[i['owner_id']].append((i['stamp'], -1))

        result: list[dict[str, typing.Any]] = []
        for pid, pool in pool_map.items():
            evs = deltas[pid]
            if not evs:
                result.append(
                    {
                        'pool': pool.name,
                        'peak': 0,
                        'peak_at': '',
                        'sessions': 0,
                    }
                )
                continue
            # Sort: process logouts before logins at equal timestamp to avoid
            # counting a 0-duration overlap between back-to-back sessions.
            evs.sort(key=lambda x: (x[0], -x[1]))
            cur = 0
            peak = 0
            peak_at = evs[0][0]
            sessions = 0
            for stamp, d in evs:
                cur += d
                if d > 0:
                    sessions += 1
                if cur > peak:
                    peak = cur
                    peak_at = stamp
            result.append(
                {
                    'pool': pool.name,
                    'peak': peak,
                    'peak_at': timezone.make_aware(datetime.datetime.fromtimestamp(peak_at)),
                    'sessions': sessions,
                }
            )

        result.sort(key=lambda r: r['peak'], reverse=True)
        return result

    def generate(self) -> bytes:
        items = self.get_data()
        return self.template_as_pdf(
            'uds/reports/stats/peak-concurrency.html',
            dct={
                'data': items,
                'beginning': self.start_date.as_date(),
                'ending': self.end_date.as_date(),
            },
            header=gettext('Peak concurrent sessions'),
            water=gettext('UDS Report of peak concurrent sessions'),
        )


class PeakConcurrencyReportCSV(PeakConcurrencyReport):
    filename = 'peak_concurrency.csv'
    mime_type = 'text/csv'
    encoded = False
    uuid = '939c621e-6d9b-4177-8b62-982284e850a5'

    pools = PeakConcurrencyReport.pools
    start_date = PeakConcurrencyReport.start_date
    end_date = PeakConcurrencyReport.end_date

    def generate(self) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([gettext('Pool'), gettext('Peak'), gettext('Peak at'), gettext('Sessions')])
        for v in self.get_data():
            writer.writerow([v['pool'], v['peak'], v['peak_at'], v['sessions']])
        return output.getvalue().encode()
