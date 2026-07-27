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
Benchmarks for the four optimised stats reports under
`uds.reports.stats`.

Each report's main data-extraction method is timed across the default
sizes, or the sizes passed in UDS_BENCH_SIZES. For every size we warm up
once (queryset compile, connection setup, ...) and then time `REPEATS`
extra runs to dilute jitter.

Reports covered:
- UsageByPool             (LOGIN/LOGOUT pairing across pools, Window+Lag)
- UsageSummaryByUsersPool (LOGIN/LOGOUT pairing for one pool, aggregated)
- PoolPerformanceReport   (ACCESS bucketization across sampling intervals)
- StatsReportLogin        (LOGIN bucketization + week/hour aggregation)

Each class also asserts a basic correctness invariant (e.g. paired
sessions == inserted pairs, total accesses == inserted ACCESS rows)
so the bench doubles as a regression guard for the optimisations.
"""

import collections.abc
import datetime
import logging
import os
import time
import typing

import pytest

from uds import models
from uds.core import types
from uds.reports.stats.pool_users_summary import UsageSummaryByUsersPool
from uds.reports.stats.pools_performance import PoolPerformanceReport
from uds.reports.stats.usage_by_pool import UsageByPool
from uds.reports.stats.user_access import StatsReportLogin

from ...fixtures import services as fixtures_services
from ...utils.test import UDSTransactionTestCase

logger = logging.getLogger(__name__)

# Benchmarks are slow (5000-row bulk inserts × 4 reports × REPEATS+warmup).
# Off by default; opt in with UDS_BENCH=1.
pytestmark = pytest.mark.skipif(
    os.environ.get("UDS_BENCH", "") in ("", "0"),
    reason="Benchmarks disabled. Set UDS_BENCH=1 to enable.",
)

# Event volumes to benchmark.
DEFAULT_SIZES: tuple[int, ...] = (50, 100, 1000, 5000)
SIZES_ENV: str = "UDS_BENCH_SIZES"


def _bench_sizes() -> tuple[int, ...]:
    raw = os.environ.get(SIZES_ENV, "").strip()
    if not raw:
        return DEFAULT_SIZES

    sizes = tuple(int(part.strip()) for part in raw.split(",") if part.strip())
    if not sizes or any(size <= 0 for size in sizes):
        raise ValueError(f"{SIZES_ENV} must contain positive integer sizes")
    return sizes


SIZES: tuple[int, ...] = _bench_sizes()
# Number of timed runs per size (excluding the warmup).
REPEATS: int = 3
# Reference epoch base for stamps; arbitrary fixed value.
BASE_STAMP: int = 1_700_000_000
# Spacing between consecutive event stamps, in seconds.
STAMP_STEP: int = 60
# Date range covering all generated benchmark stamps. The report end date is
# converted to midnight, so use the day after the last generated event.
REPORT_START_DATE: datetime.date = datetime.datetime.fromtimestamp(BASE_STAMP).date()
REPORT_END_DATE: datetime.date = datetime.datetime.fromtimestamp(
    BASE_STAMP + (max(SIZES) - 1) * STAMP_STEP
).date() + datetime.timedelta(days=1)


# --------------------------------------------------------------------------- #
# Event fixture builders                                                      #
# --------------------------------------------------------------------------- #
def _build_login_logout_pairs(pool_id: int, n_pairs: int) -> list[models.StatsEvents]:
    """N LOGIN+LOGOUT pairs for a single pool. Distinct username per pair."""
    rows: list[models.StatsEvents] = []
    login = int(types.stats.EventType.LOGIN)
    logout = int(types.stats.EventType.LOGOUT)
    owner_type = int(types.stats.EventOwnerType.SERVICEPOOL)
    for i in range(n_pairs):
        username = f"user{i}"
        login_stamp = BASE_STAMP + i * STAMP_STEP
        logout_stamp = login_stamp + 5
        rows.append(
            models.StatsEvents(
                owner_id=pool_id,
                owner_type=owner_type,
                event_type=login,
                stamp=login_stamp,
                fld2="10.0.0.1:1234",
                fld4=username,
            )
        )
        rows.append(
            models.StatsEvents(
                owner_id=pool_id,
                owner_type=owner_type,
                event_type=logout,
                stamp=logout_stamp,
                fld2="10.0.0.1:1234",
                fld4=username,
            )
        )
    return rows


def _build_access_events(pool_id: int, n_events: int) -> list[models.StatsEvents]:
    """N ACCESS events for one pool. Username cycles over ~10 distinct users."""
    rows: list[models.StatsEvents] = []
    access = int(types.stats.EventType.ACCESS)
    owner_type = int(types.stats.EventOwnerType.SERVICEPOOL)
    distinct_users = max(1, n_events // 10)
    for i in range(n_events):
        rows.append(
            models.StatsEvents(
                owner_id=pool_id,
                owner_type=owner_type,
                event_type=access,
                stamp=BASE_STAMP + i * STAMP_STEP,
                fld1=f"user{i % distinct_users}",
            )
        )
    return rows


def _build_auth_login_events(n_events: int) -> list[models.StatsEvents]:
    """N AUTHENTICATOR LOGIN events. owner_id is irrelevant (query only filters owner_type)."""
    rows: list[models.StatsEvents] = []
    login = int(types.stats.EventType.LOGIN)
    owner_type = int(types.stats.EventOwnerType.AUTHENTICATOR)
    for i in range(n_events):
        rows.append(
            models.StatsEvents(
                owner_id=1,
                owner_type=owner_type,
                event_type=login,
                stamp=BASE_STAMP + i * STAMP_STEP,
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# Bench helpers                                                               #
# --------------------------------------------------------------------------- #
def _time_callable(fn: collections.abc.Callable[[], typing.Any]) -> float:
    """Warmup once, then time REPEATS runs of fn(). Return average seconds."""
    fn()  # warmup
    timings: list[float] = []
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        fn()
        timings.append(time.perf_counter() - t0)
    return sum(timings) / len(timings)


def _print_table(report_name: str, results: list[tuple[int, float]]) -> None:
    """Pretty-print bench results as a fixed-width table to stdout."""
    header = f"{'size':>8} | {'avg ms':>10} | {'per evt us':>12}"
    sep = "-" * len(header)
    lines = [header, sep]
    for size, avg in results:
        per_evt_us = (avg / size) * 1_000_000.0 if size else 0.0
        lines.append(f"{size:>8} | {avg * 1000.0:>10.3f} | {per_evt_us:>12.3f}")
    print(f"\n{report_name} benchmark:\n" + "\n".join(lines))


# --------------------------------------------------------------------------- #
# UsageByPool                                                                  #
# --------------------------------------------------------------------------- #
class UsageByPoolBenchmark(UDSTransactionTestCase):
    pool: models.ServicePool

    def setUp(self) -> None:
        super().setUp()
        provider = fixtures_services.create_db_provider()
        service = fixtures_services.create_db_service(provider)
        self.pool = fixtures_services.create_db_servicepool(service)

    def _make_report(self) -> UsageByPool:
        report = UsageByPool()
        report.pool.value = [self.pool.uuid]  # type: ignore[assignment]
        report.start_date.value = datetime.date(2020, 1, 1)
        report.end_date.value = datetime.date(2099, 12, 31)
        return report

    def test_benchmark_get_data(self) -> None:
        results: list[tuple[int, float]] = []
        for size in SIZES:
            models.StatsEvents.objects.all().delete()
            models.StatsEvents.objects.bulk_create(
                _build_login_logout_pairs(self.pool.id, size),
                batch_size=1000,
            )
            report = self._make_report()
            warmup_data, _ = report.get_data()
            self.assertEqual(len(warmup_data), size, f"pairing wrong for size={size}")
            avg = _time_callable(lambda r=report: r.get_data())
            results.append((size, avg))
            logger.info("UsageByPool size=%d avg=%.4fs", size, avg)
        _print_table("UsageByPool", results)

    def test_pairing_semantics_stress(self) -> None:
        """LOGIN/LOGOUT pairing must be 1:1 across volumes."""
        for size in (50, 5000):
            models.StatsEvents.objects.all().delete()
            models.StatsEvents.objects.bulk_create(
                _build_login_logout_pairs(self.pool.id, size),
                batch_size=1000,
            )
            data, _ = self._make_report().get_data()
            self.assertEqual(len(data), size)
            for row in data:
                self.assertEqual(row["time"], 5)
                self.assertEqual(row["origin"], "10.0.0.1")


# --------------------------------------------------------------------------- #
# UsageSummaryByUsersPool                                                      #
# --------------------------------------------------------------------------- #
class UsageSummaryByUsersPoolBenchmark(UDSTransactionTestCase):
    pool: models.ServicePool

    def setUp(self) -> None:
        super().setUp()
        provider = fixtures_services.create_db_provider()
        service = fixtures_services.create_db_service(provider)
        self.pool = fixtures_services.create_db_servicepool(service)

    def _make_report(self) -> UsageSummaryByUsersPool:
        report = UsageSummaryByUsersPool()
        # ChoiceField (single value, not list).
        report.pool.value = self.pool.uuid  # type: ignore[assignment]
        report.start_date.value = datetime.date(2020, 1, 1)
        report.end_date.value = datetime.date(2099, 12, 31)
        return report

    def test_benchmark_get_data(self) -> None:
        results: list[tuple[int, float]] = []
        for size in SIZES:
            models.StatsEvents.objects.all().delete()
            models.StatsEvents.objects.bulk_create(
                _build_login_logout_pairs(self.pool.id, size),
                batch_size=1000,
            )
            report = self._make_report()
            warmup_data, _ = report.get_data()
            # One distinct user per pair -> #rows == #pairs.
            self.assertEqual(len(warmup_data), size, f"aggregation wrong for size={size}")
            avg = _time_callable(lambda r=report: r.get_data())
            results.append((size, avg))
            logger.info("UsageSummaryByUsersPool size=%d avg=%.4fs", size, avg)
        _print_table("UsageSummaryByUsersPool", results)

    def test_aggregation_semantics(self) -> None:
        """Per-user aggregation: 1 session, hours = 5/3600 by construction."""
        size = 100
        models.StatsEvents.objects.all().delete()
        models.StatsEvents.objects.bulk_create(
            _build_login_logout_pairs(self.pool.id, size),
            batch_size=1000,
        )
        data, name = self._make_report().get_data()
        self.assertEqual(name, self.pool.name)
        self.assertEqual(len(data), size)
        for row in data:
            self.assertEqual(row["sessions"], 1)
            self.assertEqual(row["hours"], "{:.2f}".format(5 / 3600))


# --------------------------------------------------------------------------- #
# PoolPerformanceReport                                                        #
# --------------------------------------------------------------------------- #
class PoolPerformanceBenchmark(UDSTransactionTestCase):
    pool: models.ServicePool

    SAMPLING_POINTS: int = 64

    def setUp(self) -> None:
        super().setUp()
        provider = fixtures_services.create_db_provider()
        service = fixtures_services.create_db_service(provider)
        self.pool = fixtures_services.create_db_servicepool(service)

    def _make_report(self) -> PoolPerformanceReport:
        report = PoolPerformanceReport()
        report.pools.value = [self.pool.uuid]  # type: ignore[assignment]
        report.start_date.value = REPORT_START_DATE
        report.end_date.value = REPORT_END_DATE
        report.sampling_points.value = self.SAMPLING_POINTS
        return report

    def test_benchmark_get_range_data(self) -> None:
        results: list[tuple[int, float]] = []
        for size in SIZES:
            models.StatsEvents.objects.all().delete()
            models.StatsEvents.objects.bulk_create(
                _build_access_events(self.pool.id, size),
                batch_size=1000,
            )
            report = self._make_report()
            _xfmt, pools_data, report_data = report.get_range_data()
            self.assertEqual(len(pools_data), 1)
            self.assertEqual(len(report_data), self.SAMPLING_POINTS)
            # Total accesses across buckets must equal events inserted.
            self.assertEqual(
                sum(r["accesses"] for r in report_data),
                size,
                f"accesses sum mismatch for size={size}",
            )
            avg = _time_callable(lambda r=report: r.get_range_data())
            results.append((size, avg))
            logger.info("PoolPerformanceReport size=%d avg=%.4fs", size, avg)
        _print_table("PoolPerformanceReport", results)


# --------------------------------------------------------------------------- #
# StatsReportLogin (user_access)                                               #
# --------------------------------------------------------------------------- #
class StatsReportLoginBenchmark(UDSTransactionTestCase):
    SAMPLING_POINTS: int = 64

    def _make_report(self) -> StatsReportLogin:
        report = StatsReportLogin()
        report.start_date.value = REPORT_START_DATE
        report.end_date.value = REPORT_END_DATE
        report.sampling_points.value = self.SAMPLING_POINTS
        return report

    def test_benchmark_get_range_data(self) -> None:
        results: list[tuple[int, float]] = []
        for size in SIZES:
            models.StatsEvents.objects.all().delete()
            models.StatsEvents.objects.bulk_create(
                _build_auth_login_events(size),
                batch_size=1000,
            )
            report = self._make_report()
            _xfmt, _data, report_data = report.get_range_data()
            self.assertEqual(len(report_data), self.SAMPLING_POINTS)
            self.assertEqual(
                sum(r["users"] for r in report_data),
                size,
                f"logins sum mismatch for size={size}",
            )
            avg = _time_callable(lambda r=report: r.get_range_data())
            results.append((size, avg))
            logger.info("StatsReportLogin.get_range_data size=%d avg=%.4fs", size, avg)
        _print_table("StatsReportLogin.get_range_data", results)

    def test_benchmark_get_week_hourly_data(self) -> None:
        results: list[tuple[int, float]] = []
        for size in SIZES:
            models.StatsEvents.objects.all().delete()
            models.StatsEvents.objects.bulk_create(
                _build_auth_login_events(size),
                batch_size=1000,
            )
            report = self._make_report()
            data_week, data_hour, data_week_hour = report.get_week_hourly_data()
            self.assertEqual(sum(data_week), size)
            self.assertEqual(sum(data_hour), size)
            self.assertEqual(sum(sum(r) for r in data_week_hour), size)
            avg = _time_callable(lambda r=report: r.get_week_hourly_data())
            results.append((size, avg))
            logger.info("StatsReportLogin.get_week_hourly_data size=%d avg=%.4fs", size, avg)
        _print_table("StatsReportLogin.get_week_hourly_data", results)
