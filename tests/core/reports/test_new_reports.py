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
#    * Neither the name of Virtual Cable S.L.U. nor the names of its contributors
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
Smoke tests for the 12 new reports. Each report is instantiated with a
minimal valid form payload and `generate()` is invoked. The point is to
catch syntax/template/query errors — output content is not asserted.
"""

import datetime
import logging
import random

import pytest

from uds import models
from uds.core import types
from uds.reports.lists.admin_activity import AdminActivityReport
from uds.reports.lists.admin_activity import AdminActivityReportCSV
from uds.reports.lists.failed_logins import FailedLoginsReport
from uds.reports.lists.failed_logins import FailedLoginsReportCSV
from uds.reports.lists.inactive_users import InactiveUsersReport
from uds.reports.lists.inactive_users import InactiveUsersReportCSV
from uds.reports.stats.cache_efficiency import CacheEfficiencyReport
from uds.reports.stats.cache_efficiency import CacheEfficiencyReportCSV
from uds.reports.stats.client_platforms import ClientPlatformsReport
from uds.reports.stats.client_platforms import ClientPlatformsReportCSV
from uds.reports.stats.peak_concurrency import PeakConcurrencyReport
from uds.reports.stats.peak_concurrency import PeakConcurrencyReportCSV
from uds.reports.stats.pool_saturation import PoolSaturationReport
from uds.reports.stats.pool_saturation import PoolSaturationReportCSV
from uds.reports.stats.session_duration import SessionDurationReport
from uds.reports.stats.session_duration import SessionDurationReportCSV
from uds.reports.stats.top_users import TopUsersReport
from uds.reports.stats.top_users import TopUsersReportCSV
from uds.reports.stats.tunnel_usage import TunnelUsageReport
from uds.reports.stats.tunnel_usage import TunnelUsageReportCSV
from uds.reports.stats.usage_by_group import UsageByGroupReport
from uds.reports.stats.usage_by_group import UsageByGroupReportCSV
from uds.reports.stats.userservice_errors import UserServiceErrorsReport
from uds.reports.stats.userservice_errors import UserServiceErrorsReportCSV

from ...fixtures import authenticators as fixtures_auths
from ...fixtures import services as fixtures_services
from ...utils.test import UDSTransactionTestCase

logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.filterwarnings("ignore::UserWarning"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
]


RANGE_START = datetime.date(2024, 1, 1)
RANGE_END = datetime.date(2024, 1, 31)


def _stamp(rng: random.Random) -> int:
    start = int(datetime.datetime.combine(RANGE_START, datetime.time.min).timestamp())
    end = int(datetime.datetime.combine(RANGE_END, datetime.time.max).timestamp())
    return rng.randint(start, end)


class _NewReportsBase(UDSTransactionTestCase):
    pool: models.ServicePool
    auth: models.Authenticator

    def setUp(self) -> None:
        super().setUp()
        provider = fixtures_services.create_db_provider()
        service = fixtures_services.create_db_service(provider)
        self.pool = fixtures_services.create_db_servicepool(service)
        self.auth = fixtures_auths.create_db_authenticator()

    def _seed_login_logout(self, n_pairs: int = 20) -> None:
        rng = random.Random(0)
        rows: list[models.StatsEvents] = []
        login = int(types.stats.EventType.LOGIN)
        logout = int(types.stats.EventType.LOGOUT)
        owner_type = int(types.stats.EventOwnerType.SERVICEPOOL)
        for _ in range(n_pairs):
            user = rng.choice(["alice", "bob", "carol"])
            t = _stamp(rng)
            rows.append(
                models.StatsEvents(
                    owner_id=self.pool.id,
                    owner_type=owner_type,
                    event_type=login,
                    stamp=t,
                    fld2="10.0.0.1:1234",
                    fld4=user,
                )
            )
            rows.append(
                models.StatsEvents(
                    owner_id=self.pool.id,
                    owner_type=owner_type,
                    event_type=logout,
                    stamp=t + rng.randint(60, 3600),
                    fld2="10.0.0.1:1234",
                    fld4=user,
                )
            )
        models.StatsEvents.objects.bulk_create(rows)

    def _seed_cache_events(self) -> None:
        rng = random.Random(1)
        rows: list[models.StatsEvents] = []
        owner_type = int(types.stats.EventOwnerType.SERVICEPOOL)
        for et in (types.stats.EventType.CACHE_HIT, types.stats.EventType.CACHE_MISS):
            for _ in range(10):
                rows.append(
                    models.StatsEvents(
                        owner_id=self.pool.id,
                        owner_type=owner_type,
                        event_type=int(et),
                        stamp=_stamp(rng),
                    )
                )
        models.StatsEvents.objects.bulk_create(rows)

    def _seed_tunnel_events(self) -> None:
        rng = random.Random(2)
        rows: list[models.StatsEvents] = []
        owner_type = int(types.stats.EventOwnerType.SERVICEPOOL)
        for _ in range(10):
            rows.append(
                models.StatsEvents(
                    owner_id=self.pool.id,
                    owner_type=owner_type,
                    event_type=int(types.stats.EventType.TUNNEL_OPEN),
                    stamp=_stamp(rng),
                )
            )
            rows.append(
                models.StatsEvents(
                    owner_id=self.pool.id,
                    owner_type=owner_type,
                    event_type=int(types.stats.EventType.TUNNEL_CLOSE),
                    stamp=_stamp(rng),
                    fld1="1234",
                    fld2="2048",
                    fld3="4096",
                )
            )
        models.StatsEvents.objects.bulk_create(rows)

    def _seed_platform_events(self) -> None:
        rng = random.Random(3)
        rows: list[models.StatsEvents] = []
        owner_type = int(types.stats.EventOwnerType.AUTHENTICATOR)
        for _ in range(20):
            rows.append(
                models.StatsEvents(
                    owner_id=self.auth.id,
                    owner_type=owner_type,
                    event_type=int(types.stats.EventType.PLATFORM),
                    stamp=_stamp(rng),
                    fld1=rng.choice(["Linux", "Windows", "macOS"]),
                    fld2=rng.choice(["Firefox", "Chrome", "Safari"]),
                    fld3="1.0",
                )
            )
        models.StatsEvents.objects.bulk_create(rows)


class PeakConcurrencyTest(_NewReportsBase):
    def test_generate(self) -> None:
        self._seed_login_logout()
        for cls in (PeakConcurrencyReport, PeakConcurrencyReportCSV):
            r = cls()
            r.pools.value = [self.pool.uuid]  # type: ignore[assignment]
            r.start_date.value = RANGE_START
            r.end_date.value = RANGE_END
            self.assertGreater(len(r.generate()), 0)


class PoolSaturationTest(_NewReportsBase):
    def test_generate(self) -> None:
        for cls in (PoolSaturationReport, PoolSaturationReportCSV):
            r = cls()
            r.pools.value = [self.pool.uuid]  # type: ignore[assignment]
            r.start_date.value = RANGE_START
            r.end_date.value = RANGE_END
            self.assertGreater(len(r.generate()), 0)


class CacheEfficiencyTest(_NewReportsBase):
    def test_generate(self) -> None:
        self._seed_cache_events()
        for cls in (CacheEfficiencyReport, CacheEfficiencyReportCSV):
            r = cls()
            r.pools.value = [self.pool.uuid]  # type: ignore[assignment]
            r.start_date.value = RANGE_START
            r.end_date.value = RANGE_END
            self.assertGreater(len(r.generate()), 0)


class TunnelUsageTest(_NewReportsBase):
    def test_generate(self) -> None:
        self._seed_tunnel_events()
        for cls in (TunnelUsageReport, TunnelUsageReportCSV):
            r = cls()
            r.pools.value = [self.pool.uuid]  # type: ignore[assignment]
            r.start_date.value = RANGE_START
            r.end_date.value = RANGE_END
            self.assertGreater(len(r.generate()), 0)


class ClientPlatformsTest(_NewReportsBase):
    def test_generate(self) -> None:
        self._seed_platform_events()
        for cls in (ClientPlatformsReport, ClientPlatformsReportCSV):
            r = cls()
            r.start_date.value = RANGE_START
            r.end_date.value = RANGE_END
            self.assertGreater(len(r.generate()), 0)


class TopUsersTest(_NewReportsBase):
    def test_generate(self) -> None:
        self._seed_login_logout()
        for cls in (TopUsersReport, TopUsersReportCSV):
            r = cls()
            r.start_date.value = RANGE_START
            r.end_date.value = RANGE_END
            self.assertGreater(len(r.generate()), 0)


class SessionDurationTest(_NewReportsBase):
    def test_generate(self) -> None:
        self._seed_login_logout()
        for cls in (SessionDurationReport, SessionDurationReportCSV):
            r = cls()
            r.pools.value = [self.pool.uuid]  # type: ignore[assignment]
            r.start_date.value = RANGE_START
            r.end_date.value = RANGE_END
            self.assertGreater(len(r.generate()), 0)


class UserServiceErrorsTest(_NewReportsBase):
    def test_generate(self) -> None:
        for cls in (UserServiceErrorsReport, UserServiceErrorsReportCSV):
            r = cls()
            r.pools.value = [self.pool.uuid]  # type: ignore[assignment]
            r.start_date.value = RANGE_START
            r.end_date.value = RANGE_END
            self.assertGreater(len(r.generate()), 0)


class UsageByGroupTest(_NewReportsBase):
    def test_generate(self) -> None:
        self._seed_login_logout()
        for cls in (UsageByGroupReport, UsageByGroupReportCSV):
            r = cls()
            r.authenticator.value = self.auth.uuid  # type: ignore[assignment]
            r.pools.value = [self.pool.uuid]  # type: ignore[assignment]
            r.start_date.value = RANGE_START
            r.end_date.value = RANGE_END
            self.assertGreater(len(r.generate()), 0)


class InactiveUsersTest(_NewReportsBase):
    def test_generate(self) -> None:
        for cls in (InactiveUsersReport, InactiveUsersReportCSV):
            r = cls()
            r.authenticator.value = "0-0-0-0"  # type: ignore[assignment]
            r.days.value = 30
            r.include_never.value = True
            self.assertGreater(len(r.generate()), 0)


class AdminActivityTest(_NewReportsBase):
    def test_generate(self) -> None:
        for cls in (AdminActivityReport, AdminActivityReportCSV):
            r = cls()
            r.start_date.value = RANGE_START
            r.end_date.value = RANGE_END
            r.top_paths.value = 5
            self.assertGreater(len(r.generate()), 0)


class FailedLoginsTest(_NewReportsBase):
    def test_generate(self) -> None:
        for cls in (FailedLoginsReport, FailedLoginsReportCSV):
            r = cls()
            r.authenticator.value = "0-0-0-0"  # type: ignore[assignment]
            r.start_date.value = RANGE_START
            r.end_date.value = RANGE_END
            self.assertGreater(len(r.generate()), 0)
