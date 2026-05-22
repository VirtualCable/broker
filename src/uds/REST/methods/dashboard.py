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

"""
Reports dashboard.

Aggregates the data produced by the statistics reports into a single JSON
payload so the admin GUI can render it as interactive charts/tables, instead
of having to download a PDF/CSV report for each metric.

The heavy query logic is *not* duplicated here: each report class already
exposes an optimized ``get_data()`` method, so this handler simply instantiates
the report, fills its GUI fields with the dashboard date range and reuses that
method. The assembled payload is cached for a few minutes, as the underlying
queries are expensive.

Author: Virtual Cable S.L.
"""

import datetime
import logging
import typing
import collections.abc

from uds import models
from uds.core import consts, exceptions, types
from uds.core.types.states import State
from uds.core.util.cache import Cache
from uds.core.util.model import sql_now
from uds.REST import Handler

# Report classes reused for their (already optimized) get_data() queries
from uds.reports.lists.failed_logins import FailedLoginsReport
from uds.reports.stats.base import StatsReport
from uds.reports.stats.cache_efficiency import CacheEfficiencyReport
from uds.reports.stats.client_platforms import ClientPlatformsReport
from uds.reports.stats.peak_concurrency import PeakConcurrencyReport
from uds.reports.stats.pool_saturation import PoolSaturationReport
from uds.reports.stats.session_duration import SessionDurationReport
from uds.reports.stats.top_users import TopUsersReport
from uds.reports.stats.tunnel_usage import TunnelUsageReport
from uds.reports.stats.userservice_errors import UserServiceErrorsReport

if typing.TYPE_CHECKING:
    from uds.core.reports.report import Report

logger = logging.getLogger(__name__)

cache = Cache('DashboardData')

CACHE_TIME: typing.Final[int] = 60 * 10  # 10 minutes
DEFAULT_DAYS: typing.Final[int] = 30
MIN_DAYS: typing.Final[int] = 1
MAX_DAYS: typing.Final[int] = 365
# How many rows each table-like widget exposes to the dashboard. Kept bounded
# so the JSON payload stays small; the charts add zoom/scroll to navigate them.
TOP_ROWS: typing.Final[int] = 25


def _report_data(
    report_cls: type['Report'],
    start_date: datetime.date,
    end_date: datetime.date,
) -> typing.Any:
    """
    Instantiate a stats report, fill its date range and return the result of
    its ``get_data()`` method.

    ``init_gui()`` is intentionally not called: it only populates the choice
    fields shown on the report form, which ``get_data()`` does not need.

    Reports that need extra GUI fields (TopUsersReport, FailedLoginsReport) are
    built directly by their widget builder instead of going through this helper.
    """
    # Typed loosely: report instances expose date/pool GUI fields and get_data()
    # dynamically per concrete class; the base Report does not declare them.
    report: typing.Any = report_cls()
    # Stats reports carry a "pools" MultiChoiceField; '0-0-0-0' is the ALL POOLS
    # sentinel. List reports (e.g. FailedLoginsReport) have no pools field.
    # Check report_cls (the type), not the instance, so `report` stays Any.
    if issubclass(report_cls, StatsReport):
        report.pools.value = ['0-0-0-0']
    report.start_date.value = start_date
    report.end_date.value = end_date
    return report.get_data()


class Dashboard(Handler):
    """
    Read-only reports dashboard.

    GET /dashboard            -> full dashboard payload (alias of /dashboard/data)
    GET /dashboard/data       -> full dashboard payload

    Optional query parameter ``days`` (1..365, default 30) sets the look-back
    window used by every time-bound widget.
    """

    ROLE = consts.UserRole.ADMIN

    def _kpis(self) -> dict[str, int]:
        """Fast, point-in-time counters shown on the dashboard header."""
        users_with_valid_services = models.User.objects.filter(
            userServices__state__in=State.VALID_STATES
        ).order_by()
        return {
            'users': models.User.objects.count(),
            'groups': models.Group.objects.count(),
            'users_with_services': users_with_valid_services.values('id').distinct().count(),
            'assigned_user_services': users_with_valid_services.values('id').count(),
            'service_pools': models.ServicePool.objects.count(),
            'meta_pools': models.MetaPool.objects.count(),
            'user_services': models.UserService.objects.exclude(state__in=(State.REMOVED, State.ERROR)).count(),
            'restrained_service_pools': models.ServicePool.restraineds_queryset().count(),
            'authenticators': models.Authenticator.objects.count(),
            'tunnels': models.Server.objects.filter(type=types.servers.ServerType.TUNNEL).count(),
        }

    def _widget(self, name: str, builder: collections.abc.Callable[[], typing.Any]) -> typing.Any:
        """
        Run a single widget builder, isolating failures: a broken widget must
        not take the whole dashboard down with it.
        """
        try:
            return builder()
        except Exception as e:
            logger.exception('Error building dashboard widget "%s"', name)
            return {'error': str(e)}

    def _build(self, days: int) -> dict[str, typing.Any]:
        until = sql_now()
        since = until - datetime.timedelta(days=days)
        start_date = since.date()
        # end_date is exclusive-ish on the report side; use "tomorrow" so the
        # current day is fully included.
        end_date = until.date() + datetime.timedelta(days=1)

        def peak_concurrency() -> typing.Any:
            return _report_data(PeakConcurrencyReport, start_date, end_date)[:TOP_ROWS]

        def pool_saturation() -> typing.Any:
            return _report_data(PoolSaturationReport, start_date, end_date)[:TOP_ROWS]

        def cache_efficiency() -> typing.Any:
            return _report_data(CacheEfficiencyReport, start_date, end_date)[:TOP_ROWS]

        def tunnel_usage() -> typing.Any:
            return _report_data(TunnelUsageReport, start_date, end_date)[:TOP_ROWS]

        def client_platforms() -> typing.Any:
            platforms, browsers, _combo, total = _report_data(ClientPlatformsReport, start_date, end_date)
            return {
                'platforms': platforms[:TOP_ROWS],
                'browsers': browsers[:TOP_ROWS],
                'total': total,
            }

        def top_users() -> typing.Any:
            report: typing.Any = TopUsersReport()
            report.pools.value = ['0-0-0-0']
            report.start_date.value = start_date
            report.end_date.value = end_date
            report.top_n.value = TOP_ROWS
            report.sort_by.value = 'time'
            return report.get_data()

        def session_duration() -> typing.Any:
            rows, total_sessions, total_seconds = _report_data(SessionDurationReport, start_date, end_date)
            avg_seconds = (total_seconds // total_sessions) if total_sessions else 0
            return {
                'buckets': rows,
                'total_sessions': total_sessions,
                'total_seconds': total_seconds,
                'avg_seconds': avg_seconds,
            }

        def userservice_errors() -> typing.Any:
            per_pool, _detail = _report_data(UserServiceErrorsReport, start_date, end_date)
            return per_pool[:TOP_ROWS]

        def failed_logins() -> typing.Any:
            report: typing.Any = FailedLoginsReport()
            report.start_date.value = start_date
            report.end_date.value = end_date
            report.authenticator.value = '0-0-0-0'
            summary, _detail = report.get_data()
            return summary[:TOP_ROWS]

        return {
            'days': days,
            'since': since,
            'until': until,
            # When this payload was built. Stays frozen in the cached copy, so
            # the GUI "Updated" label reflects the real data age, not the fetch time.
            'generated': until,
            'kpis': self._kpis(),
            'peak_concurrency': self._widget('peak_concurrency', peak_concurrency),
            'pool_saturation': self._widget('pool_saturation', pool_saturation),
            'cache_efficiency': self._widget('cache_efficiency', cache_efficiency),
            'tunnel_usage': self._widget('tunnel_usage', tunnel_usage),
            'client_platforms': self._widget('client_platforms', client_platforms),
            'top_users': self._widget('top_users', top_users),
            'session_duration': self._widget('session_duration', session_duration),
            'userservice_errors': self._widget('userservice_errors', userservice_errors),
            'failed_logins': self._widget('failed_logins', failed_logins),
        }

    def _data(self) -> dict[str, typing.Any]:
        # Clamp the requested window to a sane range
        try:
            days = int(typing.cast(str, self.query_params().get('days', DEFAULT_DAYS)))
        except (TypeError, ValueError):
            days = DEFAULT_DAYS
        days = max(MIN_DAYS, min(MAX_DAYS, days))

        cache_key = f'dashboard-{days}'
        # The GUI refresh button sends flush=1 to bypass the cached payload and
        # force a rebuild from fresh queries; otherwise the range/timestamps and
        # counters stay frozen for up to CACHE_TIME.
        flush = str(self.query_params().get('flush', '')).lower() in ('1', 'true', 'yes')
        if not flush:
            cached: dict[str, typing.Any] | None = cache.get(cache_key)
            if cached is not None:
                return cached

        data = self._build(days)
        cache.put(cache_key, data, CACHE_TIME)
        return data

    def get(self) -> typing.Any:
        logger.debug('Dashboard GET args: %s', self._args)
        if len(self._args) == 0 or (len(self._args) == 1 and self._args[0] == 'data'):
            return self._data()
        raise exceptions.rest.RequestError('invalid request')
