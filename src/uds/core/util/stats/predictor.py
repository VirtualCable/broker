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
# CAUSED AND ON THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""
Prediction of service pool usage based on historical stats counters.

Pure, testable helpers that build "usage profiles" from the hourly
accumulated counters (uds_stats_c_accum) and use them to forecast future
usage. The underlying idea is that human usage follows weekly patterns:
for a given service pool, the best predictor for "Tuesday at 10:00" are
the previous Tuesdays at 10:00.

All grouping is done in the *active local timezone* (as returned by
django's timezone.localtime), because human patterns are local. Stamps
stored on StatsCountersAccum mark the END of each interval; the samples
and profiles exposed here use the interval START as the time reference.

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import collections
import collections.abc
import dataclasses
import datetime
import logging
import statistics

from django.utils import timezone

from uds.core import consts
from uds.core import types
from uds.core.util.cache import Cache
from uds.core.util.model import sql_now
from uds.models import StatsCountersAccum

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class Sample:
    """A single (already loaded) stats sample for one interval."""

    when: datetime.datetime
    mean: float
    max: float
    count: int


@dataclasses.dataclass
class ProfileCell:
    """Statistics of a (day of week, hour of day) cell of a profile."""

    mean: float
    p50: float
    p75: float
    p90: float
    max: float
    n: int


@dataclasses.dataclass
class Profile:
    """Usage profile for a given owner and counter type."""

    owner_id: int
    counter_type: types.stats.CounterType
    first_sample: datetime.datetime | None
    last_sample: datetime.datetime | None
    total_samples: int
    cells: dict[tuple[int, int], ProfileCell]
    hourly: dict[int, ProfileCell]

    def cell_for(self, when: datetime.datetime) -> ProfileCell | None:
        """Returns the cell matching the local weekday/hour of *when*.

        Falls back to the per-hour cell (aggregated across all weekdays) when
        the specific (weekday, hour) cell has no data.
        """
        local = timezone.localtime(when)
        return self.cells.get((local.weekday(), local.hour)) or self.hourly.get(local.hour)


@dataclasses.dataclass
class ForecastPoint:
    """A single forecasted point."""

    when: datetime.datetime
    cell: ProfileCell | None


def _percentiles(values: list[float]) -> tuple[float, float, float]:
    """Returns (p50, p75, p90) of the given values."""
    if not values:
        return 0.0, 0.0, 0.0
    if len(values) < 2:
        v = values[0]
        return v, v, v
    cuts = statistics.quantiles(values, n=20, method="inclusive")
    return cuts[9], cuts[14], cuts[17]


def _weighted_mean(values: list[float]) -> float:
    """Mean with linear recency bias (last sample weighs more than the first)."""
    n = len(values)
    if n == 0:
        return 0.0
    if n == 1:
        return values[0]
    weights = [1.0 + consts.predictions.RECENCY_BIAS * i / (n - 1) for i in range(n)]
    total = sum(weights)
    return sum(v * w for v, w in zip(values, weights, strict=True)) / total


def _build_cell(samples: list[Sample]) -> ProfileCell:
    """Builds a ProfileCell from a list of (time-ordered) samples."""
    values = [s.mean for s in samples]
    p50, p75, p90 = _percentiles(values)
    return ProfileCell(
        mean=_weighted_mean(values),
        p50=p50,
        p75=p75,
        p90=p90,
        max=max((s.max for s in samples), default=0.0),
        n=len(samples),
    )


def build_profile(
    samples: collections.abc.Sequence[Sample],
    owner_id: int,
    counter_type: types.stats.CounterType,
) -> Profile:
    """Builds a usage profile from a sequence of samples.

    Samples are grouped by (weekday, hour) in the active local timezone. A
    secondary per-hour grouping is kept as a fallback for cells with no data.
    """
    by_cell: dict[tuple[int, int], list[Sample]] = collections.defaultdict(list)
    by_hour: dict[int, list[Sample]] = collections.defaultdict(list)
    first: datetime.datetime | None = None
    last: datetime.datetime | None = None
    total = 0

    for sample in samples:
        local = timezone.localtime(sample.when)
        by_cell[(local.weekday(), local.hour)].append(sample)
        by_hour[local.hour].append(sample)
        if first is None or sample.when < first:
            first = sample.when
        if last is None or sample.when > last:
            last = sample.when
        total += 1

    cells = {key: _build_cell(s) for key, s in by_cell.items()}
    hourly = {hour: _build_cell(s) for hour, s in by_hour.items()}

    return Profile(
        owner_id=owner_id,
        counter_type=counter_type,
        first_sample=first,
        last_sample=last,
        total_samples=total,
        cells=cells,
        hourly=hourly,
    )


def confidence(profile: Profile) -> float:
    """Returns a 0..1 confidence score for the profile.

    The score combines how much history the profile spans (up to
    TRAINING_WEEKS) and how well covered the 168 weekly cells are (up to
    MIN_SAMPLES_PER_CELL samples per cell on average).
    """
    if profile.total_samples == 0 or profile.first_sample is None or profile.last_sample is None:
        return 0.0
    weeks = (profile.last_sample - profile.first_sample).days / 7.0
    coverage = profile.total_samples / consts.predictions.CELLS_IN_WEEK
    span = max(0.0, min(1.0, weeks / consts.predictions.TRAINING_WEEKS))
    density = max(0.0, min(1.0, coverage / consts.predictions.MIN_SAMPLES_PER_CELL))
    return span * density


def forecast(
    profile: Profile,
    start: datetime.datetime,
    hours: int,
) -> list[ForecastPoint]:
    """Returns forecasted points for the *hours* hours starting at *start*.

    *start* must be timezone-aware. Each point carries the matching profile
    cell (or None when no data is available for that weekday/hour).
    """
    points: list[ForecastPoint] = []
    for i in range(max(0, hours)):
        when = start + datetime.timedelta(hours=i)
        points.append(ForecastPoint(when=when, cell=profile.cell_for(when)))
    return points


def detect_anomaly(
    profile: Profile,
    samples: collections.abc.Sequence[Sample],
) -> float:
    """Returns the mean relative deviation of *samples* vs the profile.

    A value greater than ANOMALY_THRESHOLD means the recent pattern is
    broken (e.g. holidays, a policy change, an outage). Samples without a
    matching profile cell are skipped.
    """
    deviations: list[float] = []
    for sample in samples:
        cell = profile.cell_for(sample.when)
        if cell is None:
            continue
        predicted = max(cell.mean, 1.0)
        deviations.append(abs(sample.mean - cell.mean) / predicted)
    if not deviations:
        return 0.0
    return sum(deviations) / len(deviations)


def load_samples(
    owner_id: int,
    counter_type: types.stats.CounterType,
    *,
    owner_type: types.stats.CounterOwnerType = types.stats.CounterOwnerType.SERVICEPOOL,
    since: datetime.datetime | None = None,
    to: datetime.datetime | None = None,
    interval_type: StatsCountersAccum.IntervalType = StatsCountersAccum.IntervalType.HOUR,
) -> list[Sample]:
    """Loads hourly (by default) accumulated counters from the database.

    Stamps on StatsCountersAccum mark the END of each interval; the returned
    samples use the interval START (in the active local timezone) as their
    time reference. Dummy "no data" rows (v_count == 0) are skipped.
    """
    if to is None:
        to = sql_now()
    if since is None:
        since = to - datetime.timedelta(weeks=consts.predictions.TRAINING_WEEKS)

    interval = interval_type.seconds()
    since_stamp = int(since.timestamp())
    to_stamp = int(to.timestamp())

    qs = (
        StatsCountersAccum.objects.filter(
            interval_type=interval_type,
            owner_type=owner_type,
            owner_id=owner_id,
            counter_type=counter_type,
            stamp__gt=since_stamp,
            stamp__lte=to_stamp,
        )
        .order_by("stamp")
        .values("stamp", "v_count", "v_sum", "v_max")
    )

    samples: list[Sample] = []
    for rec in qs:
        count = rec["v_count"]
        if count <= 0:
            continue
        start_utc = datetime.datetime.fromtimestamp(rec["stamp"] - interval, tz=datetime.timezone.utc)
        samples.append(
            Sample(
                when=timezone.localtime(start_utc),
                mean=rec["v_sum"] / count,
                max=rec["v_max"],
                count=count,
            )
        )
    return samples


def get_profile(
    owner_id: int,
    counter_type: types.stats.CounterType,
    *,
    owner_type: types.stats.CounterOwnerType = types.stats.CounterOwnerType.SERVICEPOOL,
) -> Profile:
    """Returns a usage profile, computing and caching it on first access.

    Profiles are cached for 30 days (see consts.predictions.PROFILE_CACHE_TIMEOUT).
    On cache miss, the profile is built from the accumulated counters and stored.
    Empty profiles (no samples) are never cached so they recompute on retry.
    """
    cache = Cache(
        consts.predictions.PROFILE_CACHE_OWNER,
        default_timeout=consts.predictions.PROFILE_CACHE_TIMEOUT,
    )
    key = f"{owner_id}-{counter_type.value}"
    cached = cache.get(key)
    if isinstance(cached, Profile):
        return cached
    samples = load_samples(owner_id, counter_type, owner_type=owner_type)
    profile = build_profile(samples, owner_id, counter_type)
    if profile.total_samples > 0:
        cache.put(key, profile, validity=consts.predictions.PROFILE_CACHE_TIMEOUT)
    return profile


@dataclasses.dataclass
class CacheSlotRecommendation:
    """Cache recommendation for a single hour-of-day slot (aggregated across all weekdays)."""

    hour: int
    verdict: str  # OK, EXCESS, STARVED, SATURATED, NO_DATA
    inuse_p50: float
    inuse_p90: float
    cached_mean: float
    action: str
    priority: str  # high, medium, low


def _aggregate_hour_cells(profile: Profile, hour: int) -> tuple[float, float, float]:
    """Returns (max_p50, max_p90, mean) across all weekdays for a given hour.

    Uses worst-case (max) for p50/p90 so recommendations are conservative,
    and mean for the typical value.
    """
    p50s: list[float] = []
    p90s: list[float] = []
    means: list[float] = []
    for dow in range(7):
        cell = profile.cells.get((dow, hour))
        if cell is not None and cell.n > 0:
            p50s.append(cell.p50)
            p90s.append(cell.p90)
            means.append(cell.mean)
    if not p50s:
        return 0.0, 0.0, 0.0
    return max(p50s), max(p90s), sum(means) / len(means)


def cache_recommendations(
    inuse_profile: Profile,
    cached_profile: Profile,
    *,
    cache_l1_srvs: int,
    cache_l2_srvs: int,
    initial_srvs: int,
    max_srvs: int,
) -> list[CacheSlotRecommendation]:
    """Builds per-hour cache recommendations from usage profiles.

    For each hour of the day (0-23), aggregates INUSE and CACHED stats
    across all weekdays (worst-case for percentiles) and compares against
    the pool's current cache configuration.

    Verdicts:
        SATURATED — p90(INUSE) reaches max_srvs (the pool's hard ceiling).
        STARVED   — p75(INUSE) >= cache_l1_srvs (cache gets fully consumed).
        EXCESS    — p90(INUSE) < cache_l1_srvs and CACHED mean > 0 (waste).
        OK        — usage is within configured bounds.
        NO_DATA   — no historical data for this hour.
    """
    recommendations: list[CacheSlotRecommendation] = []
    for hour in range(24):
        inuse_p50, inuse_p90, _inuse_mean = _aggregate_hour_cells(inuse_profile, hour)
        _, _, cached_mean = _aggregate_hour_cells(cached_profile, hour)

        if inuse_p90 == 0.0 and cached_mean == 0.0:
            recommendations.append(
                CacheSlotRecommendation(
                    hour=hour,
                    verdict="NO_DATA",
                    inuse_p50=0.0,
                    inuse_p90=0.0,
                    cached_mean=0.0,
                    action="No data available for this hour",
                    priority="low",
                )
            )
            continue

        if max_srvs > 0 and inuse_p90 >= max_srvs:
            recommendations.append(
                CacheSlotRecommendation(
                    hour=hour,
                    verdict="SATURATED",
                    inuse_p50=round(inuse_p50, 2),
                    inuse_p90=round(inuse_p90, 2),
                    cached_mean=round(cached_mean, 2),
                    action=f"INUSE p90 ({inuse_p90:.0f}) hits max_srvs ({max_srvs}). "
                    f"Consider increasing max_srvs or redistributing load.",
                    priority="high",
                )
            )
            continue

        if cache_l1_srvs > 0 and inuse_p50 >= cache_l1_srvs:
            recommendations.append(
                CacheSlotRecommendation(
                    hour=hour,
                    verdict="STARVED",
                    inuse_p50=round(inuse_p50, 2),
                    inuse_p90=round(inuse_p90, 2),
                    cached_mean=round(cached_mean, 2),
                    action=f"INUSE p50 ({inuse_p50:.0f}) >= cache_l1 ({cache_l1_srvs}). "
                    f"Consider raising cache_l1_srvs to at least {int(inuse_p90) + 1}.",
                    priority="high",
                )
            )
            continue

        if cache_l1_srvs > 0 and inuse_p90 < cache_l1_srvs and cached_mean > 0:
            recommendations.append(
                CacheSlotRecommendation(
                    hour=hour,
                    verdict="EXCESS",
                    inuse_p50=round(inuse_p50, 2),
                    inuse_p90=round(inuse_p90, 2),
                    cached_mean=round(cached_mean, 2),
                    action=f"INUSE p90 ({inuse_p90:.0f}) < cache_l1 ({cache_l1_srvs}) "
                    f"with cached mean {cached_mean:.1f}. Consider lowering cache_l1_srvs.",
                    priority="medium",
                )
            )
            continue

        recommendations.append(
            CacheSlotRecommendation(
                hour=hour,
                verdict="OK",
                inuse_p50=round(inuse_p50, 2),
                inuse_p90=round(inuse_p90, 2),
                cached_mean=round(cached_mean, 2),
                action="Usage within configured bounds",
                priority="low",
            )
        )
    return recommendations
