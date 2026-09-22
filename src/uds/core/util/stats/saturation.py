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
When a managed server is going to be saturated.

Built on top of the pool usage predictor (:mod:`uds.core.util.stats.predictor`):
the same hourly ``(weekday, hour)`` profiles, but answering a different
question. A forecast says "will the usual peak cross the line inside the next
N hours"; this module also says "at the current growth pace, in how many days
will the peak usage hit the saturation threshold".

What counts as "saturated" is intentionally a *common admin logic*, not a pile
of per-metric limits. The primary metric is the **composite load**: the
weighted blend of cpu/memory/users from the server group weights (the same
weights the internal scheduler uses), so a machine with a thousand users and a
relaxed cpu is not flagged as saturated by the users alone. The per-counter
thresholds (disk, users, connections) are secondary and configurable through
the "Stats" section of the global config.

The underlying counters (``StatsCountersAccum`` with ``owner_type=SERVER``)
are written by the ``ServersStatsCollector`` worker every ~10 minutes: cpu,
memory and disk as percentages (0-100), users and connections as absolute
counts, all sharing the interval stamp, so the hourly load can be recomposed
metric by metric.

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import collections
import collections.abc
import dataclasses
import datetime
import enum
import typing

import numpy as np
from django.utils import timezone

from uds.core import consts, types
from uds.core.util.cache import Cache
from uds.core.util.model import sql_now
from uds.models import Server, ServerGroup

from . import predictor

Sample = predictor.Sample
Profile = predictor.Profile


class SaturationStatus(enum.StrEnum):
    """Verdict of a single metric for a single server."""

    #: The usual peak (profile p90) is at or over the threshold right now
    SATURATED = "saturated"
    #: Growing, projected to cross the threshold within the horizon
    GROWING = "growing"
    #: Growing, but the crossing would happen beyond the trustworthy horizon
    GROWING_BEYOND_HORIZON = "growing_beyond_horizon"
    #: Not enough (or too noisy) history to trust a projection
    UNRELIABLE = "unreliable"
    #: Flat trend, will not saturate at the current pace
    STABLE = "stable"
    #: Shrinking trend
    SHRINKING = "shrinking"
    #: No samples at all
    NO_DATA = "no_data"


# Order matters: it is the "worst wins" collapse order for a server/group.
_STATUS_SEVERITY: typing.Final[tuple[SaturationStatus, ...]] = (
    SaturationStatus.SATURATED,
    SaturationStatus.GROWING,
    SaturationStatus.GROWING_BEYOND_HORIZON,
    SaturationStatus.UNRELIABLE,
    SaturationStatus.STABLE,
    SaturationStatus.SHRINKING,
    SaturationStatus.NO_DATA,
)


def worst_status(statuses: collections.abc.Iterable[SaturationStatus]) -> SaturationStatus:
    """Returns the most severe status of the iterable (NO_DATA if empty)."""
    present = set(statuses)
    for status in _STATUS_SEVERITY:
        if status in present:
            return status
    return SaturationStatus.NO_DATA


@dataclasses.dataclass(frozen=True)
class SaturationThresholds:
    """Effective (resolved) thresholds for one server.

    Values are in the units of their counter: ``load`` and ``disk`` are
    percentages (0-100), ``users`` and ``connections`` are absolute counts.
    A ``None`` means the metric is disabled (not evaluated at all).
    """

    load: float
    disk: float
    users: float | None
    connections: float | None


@dataclasses.dataclass(frozen=True)
class GrowthFit:
    """Growth trend of a metric, fitted over its daily series.

    ``method`` is ``"linear"`` (least squares over the daily peaks of the
    training window) or ``"annual"`` (the linear trend term of the yearly
    harmonic fit, used when there is enough history for it: it is robust
    against the annual seasonality a plain linear fit would misread).
    """

    slope_per_day: float
    intercept: float
    r2: float
    method: str
    days: int

    @property
    def growing(self) -> bool:
        return self.slope_per_day > consts.forecasts.SATURATION_STABLE_SLOPE_EPS

    @property
    def shrinking(self) -> bool:
        return self.slope_per_day < -consts.forecasts.SATURATION_STABLE_SLOPE_EPS


@dataclasses.dataclass
class MetricSaturation:
    """Full saturation picture for one metric of one server."""

    counter: types.stats.CounterType
    status: SaturationStatus
    current_p90: float
    current_max: float
    threshold: float
    growth: GrowthFit | None = None
    days_to_saturation: int | None = None
    saturation_date: datetime.datetime | None = None
    confidence: float = 0.0
    weeks_of_history: float = 0.0

    @property
    def projected(self) -> bool:
        """Whether a concrete future saturation date was computed."""
        return self.saturation_date is not None


@dataclasses.dataclass
class ForecastPoint:
    """One forecasted hour of the profile, flagged against its threshold."""

    when: datetime.datetime
    value: float
    saturated: bool


@dataclasses.dataclass
class ServerSaturation:
    """The saturation view of a single managed server."""

    server_id: int
    server_uuid: str
    label: str
    group_name: str
    weights: types.servers.ServerStatsWeights | None
    thresholds: SaturationThresholds
    metrics: list[MetricSaturation]
    forecast: dict[types.stats.CounterType, list[ForecastPoint]]

    @property
    def has_data(self) -> bool:
        return any(m.status is not SaturationStatus.NO_DATA for m in self.metrics)

    @property
    def status(self) -> SaturationStatus:
        """Worst status across the evaluated metrics."""
        return worst_status(m.status for m in self.metrics)

    def saturating_in_days(self) -> int | None:
        """Smallest days-to-saturation across metrics (None if nothing projects)."""
        values = [m.days_to_saturation for m in self.metrics if m.days_to_saturation is not None]
        return min(values) if values else None


def resolve_thresholds(group: ServerGroup | None) -> SaturationThresholds:
    """Reads the "Stats" config, applying the documented fallbacks.

    Users: a 0 global threshold falls back to the group's
    ``weights.max_expected_users`` (itself per-group configurable); with no
    group to fall back to, the metric is disabled. Connections: 0 disables it.
    """
    # Local import: config imports models and managers, and this module is
    # imported by the REST layer, where that weight is not needed at load time
    from uds.core.util.config import GlobalConfig

    users_cfg = GlobalConfig.STATS_SATURATION_USERS.as_int()
    if users_cfg > 0:
        users: float | None = float(users_cfg)
    elif group is not None:
        users = float(group.weights.max_expected_users)
    else:
        users = None

    connections_cfg = GlobalConfig.STATS_SATURATION_CONNECTIONS.as_int()

    return SaturationThresholds(
        load=float(GlobalConfig.STATS_SATURATION_LOAD.as_int()),
        disk=float(GlobalConfig.STATS_SATURATION_DISK.as_int()),
        users=users,
        connections=float(connections_cfg) if connections_cfg > 0 else None,
    )


def load_samples_for(server: Server, counter: types.stats.CounterType) -> list[Sample]:
    """Hourly samples of one server counter (delegates to the shared loader)."""
    return predictor.load_samples(
        server.id,
        counter,
        owner_type=types.stats.CounterOwnerType.SERVER,
    )


def load_composite_samples(
    cpu: collections.abc.Sequence[Sample],
    memory: collections.abc.Sequence[Sample],
    users: collections.abc.Sequence[Sample],
    weights: types.servers.ServerStatsWeights,
) -> list[Sample]:
    """Recompose the hourly weighted load (cpu/memory/users) from its parts.

    Samples are joined by their interval-start timestamp. An hour missing any
    of the three is dropped: a gap must not be misread as a zero on that
    component. The result is on the 0..100 scale, so it shares the units of the
    load threshold.
    """
    mem_by_when = {s.when: s.mean for s in memory}
    users_by_when = {s.when: s.mean for s in users}

    max_users = weights.max_expected_users or 1
    samples: list[Sample] = []
    for sample in cpu:
        mem_val = mem_by_when.get(sample.when)
        users_val = users_by_when.get(sample.when)
        if mem_val is None or users_val is None:
            continue
        users_factor = min(1.0, users_val / max_users)
        load = weights.cpu * sample.mean + weights.memory * mem_val + weights.users * 100.0 * users_factor
        samples.append(Sample(when=sample.when, mean=load, max=load, count=1))
    return sorted(samples, key=lambda s: s.when)


def daily_peaks(samples: collections.abc.Sequence[Sample]) -> list[tuple[datetime.date, float]]:
    """Daily peak of a metric: the maximum hourly sample of each local day.

    The peak (not the mean) is what can cross the saturation line, so the
    growth trend is fitted on the daily maxima.
    """
    by_day: dict[datetime.date, float] = {}
    for sample in samples:
        day = timezone.localtime(sample.when).date()
        current = by_day.get(day)
        if current is None or sample.max > current:
            by_day[day] = sample.max
    return sorted(by_day.items())


def _r2(values: list[float], predicted: list[float]) -> float:
    residual = sum((y - p) * (y - p) for y, p in zip(values, predicted, strict=True))
    mean = sum(values) / len(values)
    total = sum((y - mean) * (y - mean) for y in values)
    return 1.0 if total == 0.0 else 1.0 - residual / total


def fit_growth(samples: collections.abc.Sequence[Sample]) -> GrowthFit | None:
    """Trend of the daily peaks of a metric.

    With enough history (>= MIN_DAYS_FOR_ANNUAL_FIT days), the yearly harmonic
    fit decides: its linear trend term is robust against the annual seasonality
    (a busy season read as "growth" or a quiet one read as "shrinking"). With
    less history, a plain least-squares line over the daily peaks. None when
    neither can be fitted (less than two distinct days).
    """
    component = predictor.fit_annual_component(samples)
    if component is not None:
        daily: dict[datetime.date, list[float]] = collections.defaultdict(list)
        for sample in samples:
            daily[timezone.localtime(sample.when).date()].append(sample.mean)
        days = sorted(daily)
        means = [sum(daily[day]) / len(daily[day]) for day in days]
        predicted = [
            component.value_at(timezone.make_aware(datetime.datetime.combine(day, datetime.time(12, 0))))
            for day in days
        ]
        return GrowthFit(
            slope_per_day=component.trend_per_day,
            intercept=component.intercept,
            r2=_r2(means, predicted),
            method="annual",
            days=len(days),
        )

    peaks = daily_peaks(samples)
    if len(peaks) < 2:
        return None
    origin = peaks[0][0]
    xs = np.array([(day - origin).days for day, _ in peaks], dtype=float)
    ys = np.array([value for _, value in peaks], dtype=float)
    if xs[-1] == xs[0]:  # Every sample on the same day
        return None
    slope, intercept = np.polyfit(xs, ys, 1)
    return GrowthFit(
        slope_per_day=float(slope),
        intercept=float(intercept),
        r2=_r2(list(ys), [float(slope) * x + float(intercept) for x in xs]),
        method="linear",
        days=len(peaks),
    )


def _current_p90(profile: Profile) -> float:
    """Worst (maximum) per-hour p90 across the (weekday, hour) cells.

    The saturation reference is the *usual peak*: the p90 of the busiest
    hour-of-week cell, not the maximum over every single sample (that would
    let the worst day of the whole window saturate a healthy server).
    """
    return max((cell.p90 for cell in profile.cells.values() if cell.n > 0), default=0.0)


def _current_max(profile: Profile) -> float:
    return max((cell.max for cell in profile.cells.values() if cell.n > 0), default=0.0)


def _weeks_of_history(profile: Profile) -> float:
    if profile.first_sample is None or profile.last_sample is None:
        return 0.0
    return (profile.last_sample - profile.first_sample).days / 7.0


def evaluate_metric(
    *,
    counter: types.stats.CounterType,
    profile: Profile,
    samples: collections.abc.Sequence[Sample],
    threshold: float,
    now: datetime.datetime | None = None,
) -> MetricSaturation:
    """Classify one metric and, when growing, project the saturation date.

    The projection anchors on the *current* profile p90 ("the usual peak") and
    advances it along the fitted daily slope; the fitted intercept is not used
    for the distance, so a recent pattern change is not diluted by the oldest
    data in the window.
    """
    now = now or sql_now()
    current_p90 = _current_p90(profile)
    current_max = _current_max(profile)
    confidence = predictor.confidence(profile)
    weeks = _weeks_of_history(profile)

    if profile.total_samples == 0:
        return MetricSaturation(counter, SaturationStatus.NO_DATA, 0.0, 0.0, threshold)

    if current_p90 >= threshold:
        return MetricSaturation(
            counter,
            SaturationStatus.SATURATED,
            round(current_p90, 2),
            round(current_max, 2),
            threshold,
            days_to_saturation=0,
            saturation_date=now,
            confidence=round(confidence, 3),
            weeks_of_history=round(weeks, 1),
        )

    # A broken recent pattern (holidays, outage, policy change) makes the
    # weekly profile unreliable for projection, even if the current values are
    # still reportable.
    recent = [
        s
        for s in samples
        if s.when >= now - datetime.timedelta(days=consts.forecasts.SATURATION_ANOMALY_WINDOW_DAYS)
    ]
    anomaly = predictor.detect_anomaly(profile, recent)
    fit = fit_growth(samples)

    if (
        confidence < consts.forecasts.SATURATION_MIN_CONFIDENCE
        or anomaly >= consts.forecasts.ANOMALY_THRESHOLD
        or fit is None
    ):
        return MetricSaturation(
            counter,
            SaturationStatus.UNRELIABLE,
            round(current_p90, 2),
            round(current_max, 2),
            threshold,
            growth=fit,
            confidence=round(confidence, 3),
            weeks_of_history=round(weeks, 1),
        )

    days_to_saturation: int | None = None
    saturation_date: datetime.datetime | None = None
    if fit.growing:
        days_to_saturation = int(np.ceil((threshold - current_p90) / fit.slope_per_day))
        if days_to_saturation > consts.forecasts.SATURATION_MAX_HORIZON_DAYS:
            status = SaturationStatus.GROWING_BEYOND_HORIZON
            days_to_saturation = None
        else:
            status = SaturationStatus.GROWING
            saturation_date = now + datetime.timedelta(days=days_to_saturation)
    elif fit.shrinking:
        status = SaturationStatus.SHRINKING
    else:
        status = SaturationStatus.STABLE

    return MetricSaturation(
        counter,
        status,
        round(current_p90, 2),
        round(current_max, 2),
        threshold,
        growth=fit,
        days_to_saturation=days_to_saturation,
        saturation_date=saturation_date,
        confidence=round(confidence, 3),
        weeks_of_history=round(weeks, 1),
    )


def build_forecast(
    profile: Profile,
    threshold: float,
    now: datetime.datetime,
    hours: int,
) -> list[ForecastPoint]:
    """Hourly forecast of the weekly profile, flagged against the threshold.

    The profile has no growth term, so this answers the *window* question
    ("does the usual peak cross the line within the next N hours?"), which is
    deliberately separate from the days-to-saturation trend projection.
    """
    points: list[ForecastPoint] = []
    start = now.replace(minute=0, second=0, microsecond=0)
    for point in predictor.forecast(profile, start, hours):
        value = round(point.cell.mean, 2) if point.cell is not None else 0.0
        saturated = point.cell is not None and point.cell.p90 >= threshold
        points.append(ForecastPoint(when=point.when, value=value, saturated=saturated))
    return points


def server_saturation(
    server: Server,
    *,
    now: datetime.datetime | None = None,
    forecast_hours: int = consts.forecasts.SATURATION_FORECAST_HOURS_DEFAULT,
    metrics: collections.abc.Sequence[types.stats.CounterType] | None = None,
) -> ServerSaturation:
    """Full saturation + forecast view of a single managed server.

    *metrics* restricts which counters are evaluated (default: load plus every
    enabled configured counter). The composite load is only produced when the
    server belongs to a group (it needs the group's weights); an unmanaged or
    group-less server simply reports the counters it has.
    """
    now = now or sql_now()
    group = server.groups.first()
    thresholds = resolve_thresholds(group)

    # (counter, threshold, samples) for the metrics to evaluate
    providers: list[tuple[types.stats.CounterType, float, list[Sample]]] = []

    want_load = metrics is None or types.stats.CounterType.LOAD in metrics
    if group is not None and want_load:
        load_samples = load_composite_samples(
            load_samples_for(server, types.stats.CounterType.CPU),
            load_samples_for(server, types.stats.CounterType.MEMORY),
            load_samples_for(server, types.stats.CounterType.USERS),
            group.weights,
        )
        providers.append((types.stats.CounterType.LOAD, thresholds.load, load_samples))

    for counter, threshold in (
        (types.stats.CounterType.DISK, thresholds.disk),
        (types.stats.CounterType.USERS, thresholds.users),
        (types.stats.CounterType.CONNECTIONS, thresholds.connections),
    ):
        if metrics is not None and counter not in metrics:
            continue
        if threshold is None:
            continue
        providers.append((counter, threshold, load_samples_for(server, counter)))

    evaluated: list[MetricSaturation] = []
    forecast: dict[types.stats.CounterType, list[ForecastPoint]] = {}
    for counter, threshold, samples in providers:
        profile = predictor.build_profile(samples, server.id, counter)
        evaluated.append(
            evaluate_metric(counter=counter, profile=profile, samples=samples, threshold=threshold, now=now)
        )
        forecast[counter] = build_forecast(profile, threshold, now, forecast_hours)

    return ServerSaturation(
        server_id=server.id,
        server_uuid=server.uuid,
        label=server.hostname or server.ip or server.uuid[:8],
        group_name=group.name if group is not None else "",
        weights=group.weights if group is not None else None,
        thresholds=thresholds,
        metrics=evaluated,
        forecast=forecast,
    )


def server_saturation_cached(
    server: Server,
    *,
    forecast_hours: int = consts.forecasts.SATURATION_FORECAST_HOURS_DEFAULT,
) -> ServerSaturation:
    """:func:`server_saturation` memoized for a short window.

    The join of the composite load walks three counters and the daily fits run
    over up to two months of hourly samples: cheap, but not free when a group
    rollup repeats it over many servers. Results without data are not cached so
    a server that starts reporting shows up on the next call. The cache is
    best-effort: an unavailable backend simply recomputes.
    """
    cache = Cache(
        consts.forecasts.PROFILE_CACHE_OWNER,
        default_timeout=consts.forecasts.SATURATION_CACHE_TIMEOUT,
    )
    key = f"saturation-{server.id}-{forecast_hours}"
    cached = cache.get(key)
    if isinstance(cached, ServerSaturation):
        return cached
    result = server_saturation(server, forecast_hours=forecast_hours)
    if result.has_data:
        cache.put(key, result, validity=consts.forecasts.SATURATION_CACHE_TIMEOUT)
    return result


@dataclasses.dataclass
class GroupServerSaturation:
    """A compact per-server line of a group rollup."""

    server_id: int
    server_uuid: str
    label: str
    status: SaturationStatus
    saturating_in_days: int | None


@dataclasses.dataclass
class GroupSaturation:
    """The saturation rollup of a server group.

    With the internal load balancer spreading sessions evenly, the group
    saturates as a block: the first server to cross is the honest signal of
    the collective saturation point (the "true" collective capacity would give
    a very similar number). A wide spread in ``per_server`` (one server
    saturating far ahead of the rest) is in itself a warning that balancing is
    not distributing (pinned sessions, manual assignment, a server out of
    service), which is why the per-server list is reported and not collapsed
    away.
    """

    group_name: str
    status: SaturationStatus
    worst_server_label: str
    min_days_to_saturation: int | None
    per_server: list[GroupServerSaturation]


def group_saturation(
    group: ServerGroup,
    *,
    forecast_hours: int = consts.forecasts.SATURATION_FORECAST_HOURS_DEFAULT,
) -> GroupSaturation:
    """Rollup of the saturation of every server in a group.

    Individual server views go through :func:`server_saturation_cached`: a
    group-wide rollup re-computes every server otherwise, and the per-server
    results are the ones the single-server endpoint will serve later.
    """
    rows: list[GroupServerSaturation] = []
    for server in group.servers.all():
        view = server_saturation_cached(server, forecast_hours=forecast_hours)
        rows.append(
            GroupServerSaturation(
                server_id=view.server_id,
                server_uuid=view.server_uuid,
                label=view.label,
                status=view.status,
                saturating_in_days=view.saturating_in_days(),
            )
        )

    days = [r.saturating_in_days for r in rows if r.saturating_in_days is not None]
    min_days = min(days) if days else None

    worst_label = ""
    for candidate in rows:
        if candidate.status is SaturationStatus.SATURATED:
            worst_label = candidate.label
            break
    if not worst_label and min_days is not None:
        worst_label = next(r.label for r in rows if r.saturating_in_days == min_days)
    elif not worst_label and rows:
        worst_label = rows[0].label

    return GroupSaturation(
        group_name=group.name,
        status=worst_status(r.status for r in rows),
        worst_server_label=worst_label,
        min_days_to_saturation=min_days,
        per_server=rows,
    )
