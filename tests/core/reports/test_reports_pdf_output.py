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
Generate the four optimised stats reports as PDF (and the CSV variants
when they exist) and dump them on the user's Desktop. Useful to eyeball
the output of the reports after the Window+Lag / bucketization
optimisations.

Files written under ~/Desktop/uds-reports/:
    UsageByPool.pdf / UsageByPool.csv
    UsageSummaryByUsersPool.pdf / UsageSummaryByUsersPool.csv
    PoolPerformanceReport.pdf / PoolPerformanceReport.csv
    StatsReportLogin.pdf / StatsReportLogin.csv

Each test seeds a small but visually meaningful dataset (a few hundred
events spread across ~3 months) so the resulting graphs and tables are
non-empty.
"""
import datetime
import logging
import pathlib
import random

import pytest

from uds import models
from uds.core import types
from uds.reports.stats.pool_users_summary import (
    UsageSummaryByUsersPool,
    UsageSummaryByUsersPoolCSV,
)
from uds.reports.stats.pools_performance import (
    PoolPerformanceReport,
    PoolPerformanceReportCSV,
)
from uds.reports.stats.usage_by_pool import UsageByPool, UsageByPoolCSV
from uds.reports.stats.user_access import StatsReportLogin, StatsReportLoginCSV

from ...fixtures import services as fixtures_services
from ...utils.test import UDSTransactionTestCase


logger = logging.getLogger(__name__)

# matplotlib emits set_ticklabels UserWarning when sampling != number of
# auto-located ticks; weasyprint emits DeprecationWarnings on some systems.
# Neither is from our code, so silence them here only.
pytestmark = [
    pytest.mark.filterwarnings('ignore::UserWarning'),
    pytest.mark.filterwarnings('ignore::DeprecationWarning'),
]

# Output directory under the user's Desktop. Created if missing.
OUTPUT_DIR = pathlib.Path.home() / 'Desktop' / 'uds-reports'

# Date span for the seeded data.
RANGE_START_DATE = datetime.date(2023, 11, 1)
RANGE_END_DATE = datetime.date(2024, 1, 31)
# Unix epoch matching RANGE_START_DATE midnight (rough — exact tz isn't
# important for the visual output; events are spread evenly inside the
# date window).
RANGE_START_STAMP = int(
    datetime.datetime.combine(RANGE_START_DATE, datetime.time.min).timestamp()
)
RANGE_END_STAMP = int(
    datetime.datetime.combine(RANGE_END_DATE, datetime.time.max).timestamp()
)
RANGE_SECONDS = RANGE_END_STAMP - RANGE_START_STAMP

# Dataset sizes. Big enough to populate graphs without making the PDF huge.
N_LOGIN_LOGOUT_PAIRS = 200  # for UsageByPool / UsageSummaryByUsersPool
N_ACCESS_EVENTS = 500       # for PoolPerformanceReport
N_AUTH_LOGINS = 600         # for StatsReportLogin

USERNAMES = [
    'alice', 'bob', 'carol', 'david', 'eve', 'frank', 'grace', 'heidi',
    'ivan', 'judy', 'mallory', 'oscar', 'peggy', 'trent', 'victor', 'walter',
]


def _output_path(name: str) -> pathlib.Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / name


def _spread_stamp(rng: random.Random) -> int:
    """Random stamp uniformly distributed inside the report range."""
    return RANGE_START_STAMP + rng.randint(0, RANGE_SECONDS)


def _build_login_logout_pairs(pool_id: int, n_pairs: int, seed: int = 0) -> list[models.StatsEvents]:
    rng = random.Random(seed)
    rows: list[models.StatsEvents] = []
    login = int(types.stats.EventType.LOGIN)
    logout = int(types.stats.EventType.LOGOUT)
    owner_type = int(types.stats.EventOwnerType.SERVICEPOOL)
    for _ in range(n_pairs):
        username = rng.choice(USERNAMES)
        login_stamp = _spread_stamp(rng)
        # Sessions between 1 minute and 4 hours.
        logout_stamp = login_stamp + rng.randint(60, 4 * 3600)
        rows.append(
            models.StatsEvents(
                owner_id=pool_id,
                owner_type=owner_type,
                event_type=login,
                stamp=login_stamp,
                fld2='10.0.0.1:1234',
                fld4=username,
            )
        )
        rows.append(
            models.StatsEvents(
                owner_id=pool_id,
                owner_type=owner_type,
                event_type=logout,
                stamp=logout_stamp,
                fld2='10.0.0.1:1234',
                fld4=username,
            )
        )
    return rows


def _build_access_events(pool_id: int, n_events: int, seed: int = 1) -> list[models.StatsEvents]:
    rng = random.Random(seed)
    rows: list[models.StatsEvents] = []
    access = int(types.stats.EventType.ACCESS)
    owner_type = int(types.stats.EventOwnerType.SERVICEPOOL)
    for _ in range(n_events):
        rows.append(
            models.StatsEvents(
                owner_id=pool_id,
                owner_type=owner_type,
                event_type=access,
                stamp=_spread_stamp(rng),
                fld1=rng.choice(USERNAMES),
            )
        )
    return rows


def _build_auth_login_events(n_events: int, seed: int = 2) -> list[models.StatsEvents]:
    rng = random.Random(seed)
    rows: list[models.StatsEvents] = []
    login = int(types.stats.EventType.LOGIN)
    owner_type = int(types.stats.EventOwnerType.AUTHENTICATOR)
    for _ in range(n_events):
        rows.append(
            models.StatsEvents(
                owner_id=1,
                owner_type=owner_type,
                event_type=login,
                stamp=_spread_stamp(rng),
            )
        )
    return rows


class _ReportPDFBase(UDSTransactionTestCase):
    """Shared setup: one ServicePool + Desktop output dir."""

    pool: models.ServicePool

    def setUp(self) -> None:
        super().setUp()
        provider = fixtures_services.create_db_provider()
        service = fixtures_services.create_db_service(provider)
        self.pool = fixtures_services.create_db_servicepool(service)

    def _dump(self, filename: str, payload: bytes) -> pathlib.Path:
        path = _output_path(filename)
        path.write_bytes(payload)
        self.assertGreater(path.stat().st_size, 0, f'{filename} empty')
        logger.info('Wrote %s (%d bytes)', path, path.stat().st_size)
        return path


class UsageByPoolPDFTest(_ReportPDFBase):
    def test_generate_pdf_and_csv(self) -> None:
        models.StatsEvents.objects.bulk_create(
            _build_login_logout_pairs(self.pool.id, N_LOGIN_LOGOUT_PAIRS),
            batch_size=1000,
        )

        for cls, ext in ((UsageByPool, 'pdf'), (UsageByPoolCSV, 'csv')):
            report = cls()
            report.pool.value = [self.pool.uuid]  # type: ignore[assignment]
            report.start_date.value = RANGE_START_DATE
            report.end_date.value = RANGE_END_DATE
            self._dump(f'UsageByPool.{ext}', report.generate())


class UsageSummaryByUsersPoolPDFTest(_ReportPDFBase):
    def test_generate_pdf_and_csv(self) -> None:
        models.StatsEvents.objects.bulk_create(
            _build_login_logout_pairs(self.pool.id, N_LOGIN_LOGOUT_PAIRS),
            batch_size=1000,
        )

        for cls, ext in (
            (UsageSummaryByUsersPool, 'pdf'),
            (UsageSummaryByUsersPoolCSV, 'csv'),
        ):
            report = cls()
            # ChoiceField (single value).
            report.pool.value = self.pool.uuid  # type: ignore[assignment]
            report.start_date.value = RANGE_START_DATE
            report.end_date.value = RANGE_END_DATE
            self._dump(f'UsageSummaryByUsersPool.{ext}', report.generate())


class PoolPerformanceReportPDFTest(_ReportPDFBase):
    SAMPLING_POINTS: int = 32

    def test_generate_pdf_and_csv(self) -> None:
        models.StatsEvents.objects.bulk_create(
            _build_access_events(self.pool.id, N_ACCESS_EVENTS),
            batch_size=1000,
        )

        for cls, ext in (
            (PoolPerformanceReport, 'pdf'),
            (PoolPerformanceReportCSV, 'csv'),
        ):
            report = cls()
            report.pools.value = [self.pool.uuid]  # type: ignore[assignment]
            report.start_date.value = RANGE_START_DATE
            report.end_date.value = RANGE_END_DATE
            report.sampling_points.value = self.SAMPLING_POINTS
            self._dump(f'PoolPerformanceReport.{ext}', report.generate())


class StatsReportLoginPDFTest(_ReportPDFBase):
    SAMPLING_POINTS: int = 64

    def test_generate_pdf_and_csv(self) -> None:
        # No pool involved; events are AUTHENTICATOR-scoped.
        models.StatsEvents.objects.bulk_create(
            _build_auth_login_events(N_AUTH_LOGINS),
            batch_size=1000,
        )

        for cls, ext in (
            (StatsReportLogin, 'pdf'),
            (StatsReportLoginCSV, 'csv'),
        ):
            report = cls()
            report.start_date.value = RANGE_START_DATE
            report.end_date.value = RANGE_END_DATE
            report.sampling_points.value = self.SAMPLING_POINTS
            self._dump(f'StatsReportLogin.{ext}', report.generate())
