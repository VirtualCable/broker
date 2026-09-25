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
Author: Adolfo Gómez, dkmaster at dkmon dot com

Tests for the cluster and database clock checks (uds.checks.cluster).
"""

import datetime
import typing
from itertools import pairwise
from unittest import mock

from django.conf import settings

from uds import models
from uds.checks.cluster import _sample_db_clock
from uds.core import types
from uds.core.checks import runner as runner_module
from uds.core.util.config import GlobalConfig
from uds.core.util.model import sql_now

from ..utils.test import UDSTransactionTestCase

CHECK_IDS: typing.Final[tuple[str, ...]] = (
    "cluster-nodes-timezones",
    "cluster-node-clock-drift",
    "db-clock-spread",
)


class ClusterChecksTest(UDSTransactionTestCase):
    def _create_node(
        self,
        hostname: str,
        ip: str,
        *,
        timezone_value: str | None = settings.TIME_ZONE,
        db_offset: float | None = 0.0,
        age: datetime.timedelta = datetime.timedelta(),
    ) -> None:
        value: dict[str, typing.Any] = {"last_seen": (sql_now() - age).isoformat()}
        if timezone_value is not None:
            value["timezone"] = timezone_value
        if db_offset is not None:
            value["db_offset"] = db_offset
        models.Properties.objects.create(
            owner_id="cluster",
            owner_type="cluster",
            key=f"{hostname}|{ip}",
            value=value,
        )

    def _results(self) -> dict[str, types.checks.CheckResult]:
        results = {result.id: result for result in runner_module.run_checks(types.checks.CheckKind.AUTOMATIC)}
        for check_id in CHECK_IDS:
            self.assertIn(check_id, results)
        return results

    @typing.override
    def tearDown(self) -> None:
        # Restore shipped defaults: config values keep an in-memory copy that
        # outlives the (truncated) test database.
        GlobalConfig.MAX_CLOCK_DRIFT.set("60")
        GlobalConfig.MAX_DB_CLOCK_SPREAD.set("5")
        super().tearDown()

    # ------------------------------------------------------------------
    # cluster-nodes-timezones
    # ------------------------------------------------------------------
    def test_timezones_pass_with_no_nodes(self) -> None:
        result = self._results()["cluster-nodes-timezones"]
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.category, types.checks.CheckCategory.HEALTH)

    def test_timezones_pass_with_single_node_or_same_timezone(self) -> None:
        self._create_node("node1", "10.0.0.1")
        result = self._results()["cluster-nodes-timezones"]
        self.assertTrue(result.ok, result.message)

        self._create_node("node2", "10.0.0.2")
        result = self._results()["cluster-nodes-timezones"]
        self.assertTrue(result.ok, result.message)

    def test_timezones_fails_with_mixed_timezones(self) -> None:
        self._create_node("node1", "10.0.0.1", timezone_value="Europe/Madrid")
        self._create_node("node2", "10.0.0.2", timezone_value="UTC")

        result = self._results()["cluster-nodes-timezones"]
        self.assertFalse(result.ok, result.message)
        self.assertIn("Europe/Madrid", result.message)
        self.assertIn("UTC", result.message)
        self.assertEqual(len(result.details), 2)

    def test_timezones_ignores_stale_nodes(self) -> None:
        # A node down since before the upgrade (stale, legacy row without
        # timezone) must not be compared against fresh nodes
        self._create_node("node1", "10.0.0.1", timezone_value="Europe/Madrid")
        self._create_node(
            "old-node",
            "10.0.0.9",
            timezone_value=None,
            age=datetime.timedelta(days=30),
        )

        result = self._results()["cluster-nodes-timezones"]
        self.assertTrue(result.ok, result.message)

    # ------------------------------------------------------------------
    # cluster-node-clock-drift
    # ------------------------------------------------------------------
    def test_drift_passes_with_no_fresh_nodes(self) -> None:
        self._create_node("old-node", "10.0.0.9", db_offset=3600.0, age=datetime.timedelta(days=30))

        result = self._results()["cluster-node-clock-drift"]
        self.assertTrue(result.ok, result.message)

    def test_drift_passes_within_threshold(self) -> None:
        self._create_node("node1", "10.0.0.1", db_offset=30.0)
        self._create_node("node2", "10.0.0.2", db_offset=-30.0)

        result = self._results()["cluster-node-clock-drift"]
        self.assertTrue(result.ok, result.message)

    def test_drift_fails_above_threshold(self) -> None:
        self._create_node("node1", "10.0.0.1", db_offset=120.0)

        result = self._results()["cluster-node-clock-drift"]
        self.assertFalse(result.ok, result.message)
        self.assertIn("node1", result.message)
        self.assertEqual(len(result.details), 1)

    def test_drift_skips_nodes_without_measurement(self) -> None:
        self._create_node("node1", "10.0.0.1", db_offset=None)

        result = self._results()["cluster-node-clock-drift"]
        self.assertTrue(result.ok, result.message)

    def test_drift_threshold_is_configurable(self) -> None:
        GlobalConfig.MAX_CLOCK_DRIFT.set("10")
        self._create_node("node1", "10.0.0.1", db_offset=30.0)

        result = self._results()["cluster-node-clock-drift"]
        self.assertFalse(result.ok, result.message)

    # ------------------------------------------------------------------
    # db-clock-spread
    # ------------------------------------------------------------------
    def test_spread_passes_within_threshold(self) -> None:
        base = sql_now()
        samples = [base + datetime.timedelta(seconds=0.1 * i) for i in range(6)]

        with mock.patch("uds.checks.cluster._sample_db_clock", return_value=samples):
            result = self._results()["db-clock-spread"]

        self.assertTrue(result.ok, result.message)

    def test_spread_fails_above_threshold(self) -> None:
        base = sql_now()
        # 10 seconds of spread between first and last sample
        samples = [base + datetime.timedelta(seconds=2 * i) for i in range(6)]

        with mock.patch("uds.checks.cluster._sample_db_clock", return_value=samples):
            result = self._results()["db-clock-spread"]

        self.assertFalse(result.ok, result.message)
        self.assertIn("10.00s", result.message)

    def test_spread_threshold_is_configurable(self) -> None:
        GlobalConfig.MAX_DB_CLOCK_SPREAD.set("1")
        base = sql_now()
        samples = [base, base + datetime.timedelta(seconds=2)]

        with mock.patch("uds.checks.cluster._sample_db_clock", return_value=samples):
            result = self._results()["db-clock-spread"]

        self.assertFalse(result.ok, result.message)

    def test_sample_db_clock_returns_consistent_samples(self) -> None:
        # The real sampler, on the test database (local time fallback),
        # must return the requested number of ordered, comparable samples
        result = _sample_db_clock(3)
        self.assertEqual(len(result), 3)
        self.assertTrue(all(a <= b for a, b in pairwise(result)))
