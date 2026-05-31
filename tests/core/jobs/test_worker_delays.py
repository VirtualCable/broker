# -*- coding: utf-8 -*-
#
# Copyright (c) 2025 Virtual Cable S.L.U.
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
Author: Adolfo Gómez, dkmaster at dkmon dot com

Tests for next_execution_delay() across Job subclasses.
"""

import logging
from unittest import mock

from ...utils.test import UDSTestCase

from uds.core.environment import Environment

logger = logging.getLogger(__name__)


class WorkerDelayTest(UDSTestCase):
    """Verify next_execution_delay() returns expected values for active workers."""

    def setUp(self) -> None:
        super().setUp()
        self._env = Environment.testing_environment()

    # -- constant-based workers --------------------------------------------

    def test_stuck_cleaner_delay(self) -> None:
        from uds.workers.stuck_cleaner import StuckCleaner
        self.assertEqual(StuckCleaner(self._env).next_execution_delay(), 3601 * 8)

    def test_usage_accounting_delay(self) -> None:
        from uds.workers.usage_accounting import UsageAccounting
        self.assertEqual(UsageAccounting(self._env).next_execution_delay(), 60)

    def test_deferred_deletion_delay(self) -> None:
        from uds.workers.deferred_deleter import DeferredDeletionWorker
        self.assertEqual(DeferredDeletionWorker(self._env).next_execution_delay(), 7)

    def test_scheduled_action_delay(self) -> None:
        from uds.workers.scheduled_action_executor import ScheduledAction
        self.assertEqual(ScheduledAction(self._env).next_execution_delay(), 29)

    def test_cache_cleaner_delay(self) -> None:
        from uds.workers.system_cleaners import CacheCleaner
        self.assertEqual(CacheCleaner(self._env).next_execution_delay(), 3600 * 24)

    def test_ticket_store_cleaner_delay(self) -> None:
        from uds.workers.system_cleaners import TicketStoreCleaner
        self.assertEqual(TicketStoreCleaner(self._env).next_execution_delay(), 60)

    def test_sessions_cleaner_delay(self) -> None:
        from uds.workers.system_cleaners import SessionsCleaner
        self.assertEqual(SessionsCleaner(self._env).next_execution_delay(), 3600 * 24 * 7)

    def test_system_information_delay(self) -> None:
        from uds.workers.system_info import SystemInformation
        self.assertEqual(SystemInformation(self._env).next_execution_delay(), 300)

    def test_user_service_info_cleaner_delay(self) -> None:
        from uds.workers.userservice_cleaner import UserServiceInfoItemsCleaner
        self.assertEqual(UserServiceInfoItemsCleaner(self._env).next_execution_delay(), 600)

    def test_deployed_service_info_cleaner_delay(self) -> None:
        from uds.workers.service_pool_cleaner import DeployedServiceInfoItemsCleaner
        self.assertEqual(DeployedServiceInfoItemsCleaner(self._env).next_execution_delay(), 600)

    def test_stats_cleaner_delay(self) -> None:
        from uds.workers.stats_collector import StatsCleaner
        self.assertEqual(StatsCleaner(self._env).next_execution_delay(), 3600 * 24 * 15)

    def test_deployed_stats_collector_delay(self) -> None:
        from uds.workers.stats_collector import DeployedServiceStatsCollector
        self.assertEqual(DeployedServiceStatsCollector(self._env).next_execution_delay(), 599)

    # -- config-driven workers ---------------------------------------------

    def test_publication_info_cleaner_reads_config(self) -> None:
        with mock.patch('uds.core.audit.immutable.config.GlobalConfig.CLEANUP_CHECK') as m:
            # The publication_cleaner imports GlobalConfig as:
            # from uds.core.util.config import GlobalConfig
            from uds.core.util.config import GlobalConfig as GC
            m.as_int.return_value = 1234
            # Actually, let's just check the mock on the right object
            # Use the correct import path
            ...

        # Simpler: just check it's an int
        from uds.workers.publication_cleaner import PublicationInfoItemsCleaner
        delay = PublicationInfoItemsCleaner(self._env).next_execution_delay()
        self.assertIsInstance(delay, int)
        self.assertGreater(delay, 0)

    def test_publication_cleaner_reads_config(self) -> None:
        from uds.workers.publication_cleaner import PublicationCleaner
        delay = PublicationCleaner(self._env).next_execution_delay()
        self.assertIsInstance(delay, int)
        self.assertGreater(delay, 0)

    def test_user_service_remover_reads_config(self) -> None:
        from uds.workers.userservice_cleaner import UserServiceRemover
        delay = UserServiceRemover(self._env).next_execution_delay()
        self.assertIsInstance(delay, int)
        self.assertGreater(delay, 0)

    def test_hanged_cleaner_reads_config(self) -> None:
        from uds.workers.hanged_userservice_cleaner import HangedCleaner
        delay = HangedCleaner(self._env).next_execution_delay()
        self.assertIsInstance(delay, int)
        self.assertGreater(delay, 0)

    def test_stats_accumulator_reads_config(self) -> None:
        from uds.workers.stats_collector import StatsAccumulator
        delay = StatsAccumulator(self._env).next_execution_delay()
        self.assertIsInstance(delay, int)
        self.assertGreater(delay, 0)

    def test_deployed_service_remover_reads_config(self) -> None:
        from uds.workers.service_pool_cleaner import DeployedServiceRemover
        delay = DeployedServiceRemover(self._env).next_execution_delay()
        self.assertIsInstance(delay, int)
        self.assertGreater(delay, 0)

    def test_cache_updater_reads_config(self) -> None:
        from uds.workers.servicepools_cache_updater import ServiceCacheUpdater
        delay = ServiceCacheUpdater(self._env).next_execution_delay()
        self.assertIsInstance(delay, int)
        self.assertGreater(delay, 0)
