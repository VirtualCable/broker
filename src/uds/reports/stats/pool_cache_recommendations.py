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

"""
Cache recommendations report, built on top of the usage predictor.

Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import csv
import io
import logging
import typing

from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from uds.core import consts, types
from uds.core.ui import gui
from uds.core.util import config
from uds.core.util.stats import predictor
from uds.models import ServicePool

from .base import StatsReport

logger = logging.getLogger(__name__)

# Confidence below which the recommendations of a pool are not to be trusted
LOW_CONFIDENCE: typing.Final[float] = 0.3

BAND_NAMES: typing.Final[dict[str, str]] = {
    "morning": _("Morning"),
    "afternoon": _("Afternoon"),
    "night": _("Night"),
}


class PoolCacheRecommendationsReport(StatsReport):
    filename = "pool_cache_recommendations.pdf"
    name = _("Cache recommendations per pool")
    description = _("Predicted usage vs configured cache, with suggested cache sizes per pool and band")
    uuid = "bb343b7d-1548-407e-b572-01d088ff3afe"

    pools = StatsReport.pools

    @typing.override
    def init_gui(self) -> None:
        # Only active pools: a pool that is not being processed has nothing to tune.
        vals = [gui.choice_item("0-0-0-0", gettext("ALL POOLS"))] + [
            gui.choice_item(v.uuid, v.name)
            for v in ServicePool.objects.filter(state=types.states.State.ACTIVE).order_by("name")
            if v.uuid
        ]
        self.pools.set_choices(vals)

    def selected_pools(self) -> list[ServicePool]:
        """Returns the active pools selected on the report form."""
        pools = ServicePool.objects.select_related("service").filter(state=types.states.State.ACTIVE)
        if "0-0-0-0" not in self.pools.value:
            pools = pools.filter(uuid__in=self.pools.value)
        return list(pools.order_by("name"))

    def get_data(self) -> dict[str, typing.Any]:
        pools_data: list[dict[str, typing.Any]] = []
        usage_by_hour: dict[int, list[predictor.PoolHourUsage]] = {}

        for pool in self.selected_pools():
            inuse_profile = predictor.get_profile(pool.id, types.stats.CounterType.INUSE)
            cached_profile = predictor.get_profile(pool.id, types.stats.CounterType.CACHED)
            max_srvs = pool.max_srvs

            slots = predictor.cache_recommendations(
                inuse_profile,
                cached_profile,
                cache_l1_srvs=pool.cache_l1_srvs,
                cache_l2_srvs=pool.cache_l2_srvs,
                initial_srvs=pool.initial_srvs,
                max_srvs=max_srvs,
            )
            bands = predictor.band_recommendations(
                slots, cache_l1_srvs=pool.cache_l1_srvs, max_srvs=max_srvs
            )
            confidence = predictor.confidence(inuse_profile)

            pools_data.append(
                {
                    "pool": pool.name,
                    "initial_srvs": pool.initial_srvs,
                    "cache_l1_srvs": pool.cache_l1_srvs,
                    "cache_l2_srvs": pool.cache_l2_srvs,
                    "max_srvs": max_srvs,
                    "confidence": round(confidence, 2),
                    "low_confidence": confidence < LOW_CONFIDENCE,
                    "has_data": inuse_profile.total_samples > 0,
                    "verdict": predictor.worst_verdict(slot.verdict for slot in slots),
                    "bands": [_as_band_row(band) for band in bands],
                    "slots": [_as_slot_row(slot) for slot in slots],
                    "calendar_actions": _calendar_actions(bands),
                }
            )

            for slot, band in _slots_with_band(slots, bands):
                usage_by_hour.setdefault(slot.hour, []).append(
                    predictor.PoolHourUsage(
                        pool_name=pool.name,
                        verdict=slot.verdict,
                        inuse_p90=slot.inuse_p90,
                        cache_l1_srvs=pool.cache_l1_srvs,
                        suggested_cache_l1=band.suggested_cache_l1,
                    )
                )

        severity = {verdict: i for i, verdict in enumerate(predictor.VERDICT_SEVERITY)}
        pools_data.sort(key=lambda p: severity.get(p["verdict"], len(severity)))

        return {
            "pools": pools_data,
            "cross_pool": [_as_cross_pool_row(note) for note in predictor.cross_pool_notes(usage_by_hour)],
            "training_weeks": consts.predictions.TRAINING_WEEKS,
            "stats_duration": config.GlobalConfig.STATS_DURATION.as_int(),
        }

    @typing.override
    def generate(self) -> bytes:
        return self.template_as_pdf(
            "uds/reports/stats/pool-cache-recommendations.html",
            dct=self.get_data(),
            header=gettext("Cache recommendations per pool"),
            water=gettext("UDS Report of cache recommendations"),
        )


def _as_band_row(band: predictor.BandRecommendation) -> dict[str, typing.Any]:
    return {
        "band": gettext(str(BAND_NAMES.get(band.band, band.band))),
        "hours": f"{band.hours[0]:02d}:00 - {(band.hours[-1] + 1) % 24:02d}:00",
        "verdict": band.verdict,
        "inuse_p50": band.inuse_p50,
        "inuse_p90": band.inuse_p90,
        "cached_mean": band.cached_mean,
        "current_cache_l1": band.current_cache_l1,
        "suggested_cache_l1": band.suggested_cache_l1,
        "reason": band.reason,
    }


def _as_slot_row(slot: predictor.CacheSlotRecommendation) -> dict[str, typing.Any]:
    return {
        "hour": f"{slot.hour:02d}:00",
        "verdict": slot.verdict,
        "inuse_p50": slot.inuse_p50,
        "inuse_p90": slot.inuse_p90,
        "cached_mean": slot.cached_mean,
        "action": slot.action,
        "priority": slot.priority,
    }


def _as_cross_pool_row(note: predictor.CrossPoolHourNote) -> dict[str, typing.Any]:
    return {
        "hour": f"{note.hour:02d}:00",
        "hungry": ", ".join(p.pool_name for p in note.hungry),
        "surplus": ", ".join(p.pool_name for p in note.surplus),
        "lendable": note.lendable,
    }


def _slots_with_band(
    slots: typing.Sequence[predictor.CacheSlotRecommendation],
    bands: typing.Sequence[predictor.BandRecommendation],
) -> typing.Iterator[tuple[predictor.CacheSlotRecommendation, predictor.BandRecommendation]]:
    """Yields every slot along with the band it belongs to."""
    band_of_hour = {hour: band for band in bands for hour in band.hours}
    for slot in slots:
        band = band_of_hour.get(slot.hour)
        if band is not None:
            yield slot, band


def _calendar_actions(bands: typing.Sequence[predictor.BandRecommendation]) -> list[dict[str, typing.Any]]:
    """Turns the bands that need a change into calendar action suggestions.

    The report only proposes: the administrator creates the calendar action
    after reviewing it. Bands that keep their current size produce nothing.
    """
    return [
        {
            "action": consts.calendar.CALENDAR_ACTION_CACHE_L1["id"],
            "band": band.band,
            "hours": f"{band.hours[0]:02d}:00 - {(band.hours[-1] + 1) % 24:02d}:00",
            "size": band.suggested_cache_l1,
        }
        for band in bands
        if band.suggested_cache_l1 != band.current_cache_l1
    ]


class PoolCacheRecommendationsReportCSV(PoolCacheRecommendationsReport):
    filename = "pool_cache_recommendations.csv"
    mime_type = "text/csv"
    encoded = False
    uuid = "b059c8f5-ab7f-46bc-ae93-4365662250d4"

    pools = PoolCacheRecommendationsReport.pools

    @typing.override
    def generate(self) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                gettext("Pool"),
                gettext("Band"),
                gettext("Hours"),
                gettext("Verdict"),
                gettext("INUSE p50"),
                gettext("INUSE p90"),
                gettext("Cached mean"),
                gettext("Current L1"),
                gettext("Suggested L1"),
                gettext("Reason"),
            ]
        )
        for pool in self.get_data()["pools"]:
            for band in pool["bands"]:
                writer.writerow(
                    [
                        pool["pool"],
                        band["band"],
                        band["hours"],
                        band["verdict"],
                        band["inuse_p50"],
                        band["inuse_p90"],
                        band["cached_mean"],
                        band["current_cache_l1"],
                        band["suggested_cache_l1"],
                        band["reason"],
                    ]
                )
        return output.getvalue().encode()
