# -*- coding: utf-8 -*-
#
# Copyright (c) 2022-2026 Virtual Cable S.L.
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

Integration tests for the Scheduler runtime (execute_job, release_own_schedules,
JobThread with real database).
"""

import platform
import time
import typing
from unittest import mock

from django.test import TransactionTestCase
from django.utils import timezone

from uds.core.jobs import scheduler
from uds.core.jobs.jobs_factory import JobsFactory
from uds.core.jobs.job import Job
from uds.core.environment import Environment
from uds.models.scheduler import Scheduler as DBScheduler
from uds.core.types.states import State
from uds.core.util.model import sql_now


class _CountingJob(Job):
    """A Job that counts executions and has a configurable delay."""

    friendly_name = "_TestCountingJob"
    run_count = 0

    def __init__(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        super().__init__(*args, **kwargs)
        self._delay = 42

    def next_execution_delay(self) -> int:
        return self._delay

    def run(self) -> None:
        _CountingJob.run_count += 1


class _FailingJob(Job):
    friendly_name = "_TestFailingJob"

    def run(self) -> None:
        raise RuntimeError("Simulated job failure")


class _BlockingJob(Job):
    friendly_name = "_TestBlockingJob"

    def run(self) -> None:
        time.sleep(60)  # Simulated long-running job


class SchedulerIntegrationTest(TransactionTestCase):
    """Integration tests using real database."""

    def setUp(self) -> None:
        super().setUp()
        scheduler.Scheduler.granularity = 0.1  # type: ignore
        _CountingJob.run_count = 0
        # Register test jobs
        for cls in (_CountingJob, _FailingJob, _BlockingJob):
            JobsFactory.factory().register(cls.friendly_name, cls)

    def tearDown(self) -> None:
        # Release any locked rows from this test
        scheduler.Scheduler.release_own_schedules()
        super().tearDown()

    # == Scheduler singleton =============================================

    def test_scheduler_singleton(self) -> None:
        s1 = scheduler.Scheduler.scheduler()
        s2 = scheduler.Scheduler.scheduler()
        self.assertIs(s1, s2)

    def test_scheduler_notify_termination(self) -> None:
        sch = scheduler.Scheduler()
        sch.notify_termination()
        self.assertFalse(sch._keep_running)

    # == execute_job picks up due jobs ===================================

    def test_execute_job_picks_due_job(self) -> None:
        import uuid

        unique_name = f"_TestDueJob_{uuid.uuid4().hex[:8]}"
        JobsFactory.factory().register(unique_name, _CountingJob)

        # Create a Scheduler row whose next_execution is in the past
        DBScheduler.objects.create(
            name=unique_name,
            last_execution=sql_now() - timezone.timedelta(seconds=120),
            next_execution=sql_now() - timezone.timedelta(seconds=10),
            owner_server="",
            state=State.FOR_EXECUTE,
        )
        sch = scheduler.Scheduler()
        with mock.patch("uds.core.jobs.scheduler.JobThread.start") as mock_start:
            sch.execute_job()
            mock_start.assert_called_once()

    def test_execute_job_skips_future_job(self) -> None:
        import uuid

        unique_name = f"_TestFutureJob_{uuid.uuid4().hex[:8]}"
        JobsFactory.factory().register(unique_name, _CountingJob)

        # Job whose next_execution is still in the future
        DBScheduler.objects.create(
            name=unique_name,
            last_execution=sql_now() - timezone.timedelta(seconds=60),
            next_execution=sql_now() + timezone.timedelta(seconds=3600),
            owner_server="",
            state=State.FOR_EXECUTE,
        )
        sch = scheduler.Scheduler()
        with mock.patch("uds.core.jobs.scheduler.JobThread.start") as mock_start:
            sch.execute_job()
            mock_start.assert_not_called()

    def test_execute_job_clock_skew_triggers_execution(self) -> None:
        """If last_execution is in the future, the job should be picked up (clock skew)."""
        import uuid

        unique_name = f"_TestClockSkew_{uuid.uuid4().hex[:8]}"
        JobsFactory.factory().register(unique_name, _CountingJob)

        DBScheduler.objects.create(
            name=unique_name,
            last_execution=sql_now() + timezone.timedelta(seconds=3600),
            next_execution=sql_now() + timezone.timedelta(seconds=7200),
            owner_server="",
            state=State.FOR_EXECUTE,
        )
        sch = scheduler.Scheduler()
        with mock.patch("uds.core.jobs.scheduler.JobThread.start") as mock_start:
            sch.execute_job()
            mock_start.assert_called_once()

    def test_execute_job_claims_db_row(self) -> None:
        """After execute_job picks up a row, it should be marked RUNNING with owner_server."""
        import uuid

        unique_name = f"_TestClaim_{uuid.uuid4().hex[:8]}"
        JobsFactory.factory().register(unique_name, _CountingJob)

        DBScheduler.objects.create(
            name=unique_name,
            last_execution=sql_now() - timezone.timedelta(seconds=120),
            next_execution=sql_now() - timezone.timedelta(seconds=10),
            owner_server="",
            state=State.FOR_EXECUTE,
        )
        sch = scheduler.Scheduler()
        sch.execute_job()
        row = DBScheduler.objects.get(name=unique_name)
        self.assertEqual(row.state, State.RUNNING)
        self.assertEqual(row.owner_server, platform.node())

    # == release_own_schedules ===========================================

    def test_release_own_schedules_clears_owner(self) -> None:
        DBScheduler.objects.create(
            name=_CountingJob.friendly_name,
            last_execution=sql_now() - timezone.timedelta(seconds=120),
            next_execution=sql_now() + timezone.timedelta(seconds=10),
            owner_server=platform.node(),
            state=State.RUNNING,
        )
        scheduler.Scheduler.release_own_schedules()
        row = DBScheduler.objects.get(name=_CountingJob.friendly_name)
        self.assertEqual(row.owner_server, "")

    def test_release_stale_running_releases_and_resets(self) -> None:
        """Jobs RUNNING for >15 min should be reset to FOR_EXECUTE."""
        DBScheduler.objects.create(
            name=_CountingJob.friendly_name,
            last_execution=sql_now() - timezone.timedelta(minutes=20),
            next_execution=sql_now() + timezone.timedelta(hours=1),
            owner_server="other-server",
            state=State.RUNNING,
        )
        scheduler.Scheduler.release_own_schedules()
        row = DBScheduler.objects.get(name=_CountingJob.friendly_name)
        self.assertEqual(row.state, State.FOR_EXECUTE)
        self.assertEqual(row.owner_server, "")

    # == JobThread with real DB ==========================================

    def test_job_thread_updates_db_on_completion(self) -> None:
        db_job = DBScheduler.objects.create(
            name=_CountingJob.friendly_name,
            last_execution=sql_now() - timezone.timedelta(seconds=120),
            next_execution=sql_now() - timezone.timedelta(seconds=10),
            owner_server="",
            state=State.FOR_EXECUTE,
        )
        job_instance = _CountingJob(Environment.testing_environment())
        thread = scheduler.JobThread(job_instance, db_job)
        thread.start()
        thread.join(timeout=5)

        row = DBScheduler.objects.get(id=db_job.id)
        self.assertEqual(row.state, State.FOR_EXECUTE)
        self.assertEqual(row.owner_server, "")
        # next_execution should be sql_now + 42
        expected = sql_now() + timezone.timedelta(seconds=42)
        diff = abs((row.next_execution - expected).total_seconds())
        self.assertLess(diff, 5)  # within 5 seconds

    def test_job_thread_updates_db_even_on_exception(self) -> None:
        db_job = DBScheduler.objects.create(
            name=_FailingJob.friendly_name,
            last_execution=sql_now() - timezone.timedelta(seconds=120),
            next_execution=sql_now() - timezone.timedelta(seconds=10),
            owner_server="",
            state=State.FOR_EXECUTE,
        )
        job_instance = _FailingJob(Environment.testing_environment())
        thread = scheduler.JobThread(job_instance, db_job)
        thread.start()
        thread.join(timeout=5)

        row = DBScheduler.objects.get(id=db_job.id)
        self.assertEqual(row.state, State.FOR_EXECUTE)
        self.assertEqual(row.owner_server, "")
