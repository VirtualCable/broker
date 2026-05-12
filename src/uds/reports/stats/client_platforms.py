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

from django.db.models import Count
from django.utils.translation import gettext, gettext_lazy as _

from uds.core.managers.stats import StatsManager
from uds.core.util import stats

from .base import StatsReport

logger = logging.getLogger(__name__)


class ClientPlatformsReport(StatsReport):
    filename = 'client_platforms.pdf'
    name = _('Client platforms breakdown')
    description = _('Breakdown of client platforms, browsers and versions seen on web logins')
    uuid = '5aa4bcf9-cbff-4e9a-81a3-16915347a77a'

    start_date = StatsReport.start_date
    end_date = StatsReport.end_date

    def get_data(
        self,
    ) -> tuple[
        list[dict[str, typing.Any]],
        list[dict[str, typing.Any]],
        list[dict[str, typing.Any]],
        int,
    ]:
        start = self.start_date.as_timestamp()
        end = self.end_date.as_timestamp()

        platform_event = stats.events.types.stats.EventType.PLATFORM
        # PLATFORM event: fld1=platform, fld2=browser, fld3=version
        rows = list(
            StatsManager.manager()
            .enumerate_events(
                stats.events.types.stats.EventOwnerType.AUTHENTICATOR,
                platform_event,
                since=start,
                to=end,
            )
            .values('fld1', 'fld2', 'fld3')
            .annotate(c=Count('id'))
        )

        total = sum(r['c'] for r in rows)

        platforms: dict[str, int] = collections.defaultdict(int)
        browsers: dict[str, int] = collections.defaultdict(int)
        combo: list[dict[str, typing.Any]] = []
        for r in rows:
            p = r['fld1'] or gettext('Unknown')
            b = r['fld2'] or gettext('Unknown')
            v = r['fld3']
            platforms[p] += r['c']
            browsers[b] += r['c']
            combo.append({'platform': p, 'browser': b, 'version': v, 'count': r['c']})

        def _pct(n: int) -> str:
            return '{:.1f}'.format(n * 100.0 / total) if total else '0.0'

        platforms_list = sorted(
            ({'name': k, 'count': v, 'pct': _pct(v)} for k, v in platforms.items()),
            key=lambda r: r['count'],
            reverse=True,
        )
        browsers_list = sorted(
            ({'name': k, 'count': v, 'pct': _pct(v)} for k, v in browsers.items()),
            key=lambda r: r['count'],
            reverse=True,
        )
        combo.sort(key=lambda r: r['count'], reverse=True)

        return platforms_list, browsers_list, combo, total

    def generate(self) -> bytes:
        platforms, browsers, combo, total = self.get_data()
        return self.template_as_pdf(
            'uds/reports/stats/client-platforms.html',
            dct={
                'platforms': platforms,
                'browsers': browsers,
                'combo': combo,
                'total': total,
                'beginning': self.start_date.as_date(),
                'ending': self.end_date.as_date(),
            },
            header=gettext('Client platforms breakdown'),
            water=gettext('UDS Report of client platforms'),
        )


class ClientPlatformsReportCSV(ClientPlatformsReport):
    filename = 'client_platforms.csv'
    mime_type = 'text/csv'
    encoded = False
    uuid = '26638387-4d35-4dd1-a7d6-bd45d0c3dcf4'

    start_date = ClientPlatformsReport.start_date
    end_date = ClientPlatformsReport.end_date

    def generate(self) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                gettext('Platform'),
                gettext('Browser'),
                gettext('Version'),
                gettext('Count'),
            ]
        )
        _platforms, _browsers, combo, _total = self.get_data()
        for v in combo:
            writer.writerow([v['platform'], v['browser'], v['version'], v['count']])
        return output.getvalue().encode()
