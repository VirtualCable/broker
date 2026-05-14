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
import io
import logging
import typing

from django.db.models import Count
from django.utils.translation import gettext, gettext_lazy as _

from uds.core.managers.stats import StatsManager
from uds.core.ui import gui
from uds.core.util import stats
from uds.models import ServicePool

from .base import StatsReport

logger = logging.getLogger(__name__)


class CacheEfficiencyReport(StatsReport):
    filename = 'cache_efficiency.pdf'
    name = _('Pool cache efficiency')
    description = _('Cache hit/miss ratio per pool over a period')
    uuid = '7305fcca-41ce-45ce-bb3a-3579251fb34a'

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
            qs = ServicePool.objects.all()
        else:
            qs = ServicePool.objects.filter(uuid__in=self.pools.value)

        pool_map: dict[int, str] = dict(qs.values_list('id', 'name'))
        if not pool_map:
            return []

        hit = stats.events.types.stats.EventType.CACHE_HIT
        miss = stats.events.types.stats.EventType.CACHE_MISS

        rows = (
            StatsManager.manager()
            .enumerate_events(
                stats.events.types.stats.EventOwnerType.SERVICEPOOL,
                (hit, miss),
                owner_id=list(pool_map.keys()),
                since=start,
                to=end,
            )
            .values('owner_id', 'event_type')
            .annotate(c=Count('id'))
        )

        agg: dict[int, dict[int, int]] = {pid: {hit: 0, miss: 0} for pid in pool_map}
        for r in rows:
            agg[r['owner_id']][r['event_type']] = r['c']

        result: list[dict[str, typing.Any]] = []
        for pid, pool_name in pool_map.items():
            hits = agg[pid][hit]
            misses = agg[pid][miss]
            total = hits + misses
            ratio = (hits * 100.0 / total) if total else 0.0
            result.append(
                {
                    'pool': pool_name,
                    'hits': hits,
                    'misses': misses,
                    'total': total,
                    'ratio': '{:.1f}'.format(ratio),
                    'ratio_value': ratio,
                }
            )

        result.sort(key=lambda r: r['total'], reverse=True)
        return result

    def generate(self) -> bytes:
        items = self.get_data()
        return self.template_as_pdf(
            'uds/reports/stats/cache-efficiency.html',
            dct={
                'data': items,
                'beginning': self.start_date.as_date(),
                'ending': self.end_date.as_date(),
            },
            header=gettext('Pool cache efficiency'),
            water=gettext('UDS Report of cache efficiency'),
        )


class CacheEfficiencyReportCSV(CacheEfficiencyReport):
    filename = 'cache_efficiency.csv'
    mime_type = 'text/csv'
    encoded = False
    uuid = '68cb4370-6ff2-4846-87a7-da59acbd89a2'

    pools = CacheEfficiencyReport.pools
    start_date = CacheEfficiencyReport.start_date
    end_date = CacheEfficiencyReport.end_date

    def generate(self) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                gettext('Pool'),
                gettext('Hits'),
                gettext('Misses'),
                gettext('Total'),
                gettext('Hit ratio %'),
            ]
        )
        for v in self.get_data():
            writer.writerow([v['pool'], v['hits'], v['misses'], v['total'], v['ratio']])
        return output.getvalue().encode()
