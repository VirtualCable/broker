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

Unit tests for JobThread — verifies that next_execution_delay() is used
correctly when updating the DB after a job completes.
"""

import typing

from unittest import mock

from uds.core.environment import Environment
from uds.core.jobs.job import Job
from uds.core.jobs.scheduler import JobThread

from ...utils.test import UDSTestCase


class _FakeJob(Job):
    friendly_name = "Fake"

    def __init__(self, environment: Environment, delay: int = 42) -> None:
        super().__init__(environment)
        self._delay = delay
        self.executed = False

    @typing.override
    def next_execution_delay(self) -> int:
        return self._delay

    @typing.override
    def run(self) -> None:
        self.executed = True


class JobThreadTest(UDSTestCase):
    """Tests for JobThread."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self._env = Environment.testing_environment()

    def test_captures_delay_from_next_execution_delay(self) -> None:
        """JobThread should capture self._delay from job_instance.next_execution_delay()."""
        job = _FakeJob(self._env, delay=1234)
        db_job = mock.MagicMock(spec=["id"])
        db_job.id = 999

        thread = JobThread(job, db_job)
        self.assertEqual(thread._delay, 1234)

    def test_update_db_record_uses_correct_delay(self) -> None:
        """
        ``_update_db_record`` must set ``next_execution`` to
        ``sql_now() + timedelta(seconds=self._delay)``.

        We mock ``sql_now`` in the scheduler module to return a fixed
        datetime so the test is deterministic and does not depend on the
        wall clock nor on the centisecond-truncation behaviour of the real
        ``sql_now()``. This also avoids a real DB round-trip per process
        (TimeTrack cache).
        """
        import datetime
        import zoneinfo

        fixed_now = datetime.datetime(
            2026, 1, 1, 12, 0, 0, tzinfo=zoneinfo.ZoneInfo("UTC")
        )

        job = _FakeJob(self._env, delay=600)
        db_job = mock.MagicMock(spec=["id"])
        db_job.id = 999

        thread = JobThread(job, db_job)

        with (
            mock.patch("uds.core.jobs.scheduler.DBScheduler") as mock_model,
            mock.patch(
                "uds.core.jobs.scheduler.sql_now", return_value=fixed_now
            ),
        ):
            thread._update_db_record()

        # Check the filter and update
        mock_model.objects.select_for_update.assert_called_once()
        update_call = (
            mock_model.objects.select_for_update.return_value.filter.return_value.update.call_args
        )
        self.assertIsNotNone(update_call)
        # update() is invoked with keyword arguments only
        kwargs: dict[str, typing.Any] = update_call.kwargs or {}
        self.assertEqual(kwargs.get("state"), "X")  # FOR_EXECUTE
        self.assertEqual(kwargs.get("owner_server"), "")

        # next_execution must equal the mocked sql_now() + 600s, exactly.
        expected_next = fixed_now + datetime.timedelta(seconds=600)
        actual_next = kwargs.get("next_execution")
        self.assertIsNotNone(actual_next)
        self.assertEqual(actual_next, expected_next)
