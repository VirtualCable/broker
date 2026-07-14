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

from django.utils.translation import gettext, gettext_lazy as _
from django.utils import timezone

from uds.core import consts
from uds.core.ui import gui
from uds.core.util.stats import counters
from uds.models import ServicePool

from .base import StatsReport

logger = logging.getLogger(__name__)


class PoolSaturationReport(StatsReport):
    filename = 'pool_saturation.pdf'
    name = _('Pool saturation')
    description = _('Peak ASSIGNED user services vs max capacity (%) per pool over a period')
    uuid = '315921b4-b838-4178-a312-7bb75a8d58c4'

    pools = StatsReport.pools
    start_date = StatsReport.start_date
    end_date = StatsReport.end_date

    @typing.override
    def init_gui(self) -> None:
        vals = [gui.choice_item('0-0-0-0', gettext('ALL POOLS'))] + [
            gui.choice_item(v.uuid, v.name) for v in ServicePool.objects.all().order_by('name') if v.uuid
        ]
        self.pools.set_choices(vals)

    def get_data(self) -> list[dict[str, typing.Any]]:
        # select_related('service'): get_max() walks self.service.get_instance().
        if '0-0-0-0' in self.pools.value:
            pools = list(ServicePool.objects.select_related('service').all())
        else:
            pools = list(
                ServicePool.objects.select_related('service').filter(uuid__in=self.pools.value)
            )

        start_dt = datetime.datetime.combine(self.start_date.as_date(), datetime.time.min)
        start_dt = timezone.make_aware(start_dt)
        end_dt = datetime.datetime.combine(self.end_date.as_date(), datetime.time.max)
        end_dt = timezone.make_aware(end_dt)

        result: list[dict[str, typing.Any]] = []
        for pool in pools:
            peak = 0
            peak_at: datetime.datetime | None = None
            total = 0
            samples = 0
            for stamp, val in counters.enumerate_counters(
                pool,
                counters.types.stats.CounterType.ASSIGNED,
                since=start_dt,
                to=end_dt,
                interval=3600,
                use_max=True,
            ):
                v = int(val)
                total += v
                samples += 1
                if v > peak:
                    peak = v
                    peak_at = stamp

            max_user_services = pool.get_max()
            if max_user_services == consts.UNLIMITED:
                pct = 0.0
                max_str = gettext('Unlimited')
            else:
                pct = (peak * 100.0 / max_user_services) if max_user_services else 0.0
                max_str = str(max_user_services)

            avg = (total / samples) if samples else 0.0

            result.append(
                {
                    'pool': pool.name,
                    'peak': peak,
                    'peak_at': peak_at if peak_at else '',
                    'avg': '{:.2f}'.format(avg),
                    'max': max_str,
                    'pct': '{:.1f}'.format(pct),
                    'pct_value': pct,
                }
            )

        result.sort(key=lambda r: r['pct_value'], reverse=True)
        return result

    @typing.override
    def generate(self) -> bytes:
        items = self.get_data()
        return self.template_as_pdf(
            'uds/reports/stats/pool-saturation.html',
            dct={
                'data': items,
                'beginning': self.start_date.as_date(),
                'ending': self.end_date.as_date(),
            },
            header=gettext('Pool saturation'),
            water=gettext('UDS Report of pool saturation'),
        )


class PoolSaturationReportCSV(PoolSaturationReport):
    filename = 'pool_saturation.csv'
    mime_type = 'text/csv'
    encoded = False
    uuid = '7dc8eab2-4fae-454f-9dfe-8a4f4d96f106'

    pools = PoolSaturationReport.pools
    start_date = PoolSaturationReport.start_date
    end_date = PoolSaturationReport.end_date

    @typing.override
    def generate(self) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                gettext('Pool'),
                gettext('Peak'),
                gettext('Peak at'),
                gettext('Average'),
                gettext('Max'),
                gettext('Saturation %'),
            ]
        )
        for v in self.get_data():
            writer.writerow([v['pool'], v['peak'], v['peak_at'], v['avg'], v['max'], v['pct']])
        return output.getvalue().encode()
