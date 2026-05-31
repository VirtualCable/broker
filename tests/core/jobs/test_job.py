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

Unit tests for the Job base class (next_execution_delay, execute, run).
"""

import logging

from ...utils.test import UDSTestCase

from uds.core.jobs.job import Job
from uds.core.environment import Environment

logger = logging.getLogger(__name__)


class _DummyJob(Job):
    friendly_name = 'Dummy Job'

    def __init__(self, environment: Environment) -> None:
        super().__init__(environment)
        self.run_called = False
        self.run_should_fail = False

    def run(self) -> None:
        self.run_called = True
        if self.run_should_fail:
            raise RuntimeError('Simulated failure')


class JobBaseTest(UDSTestCase):
    """Tests for Job base class."""

    def setUp(self) -> None:
        super().setUp()
        self._env = Environment.testing_environment()

    def test_next_execution_delay_default(self) -> None:
        """Default next_execution_delay() should return 86400 (24h)."""
        job = Job(self._env)
        self.assertEqual(job.next_execution_delay(), 86400)

    def test_execute_calls_run(self) -> None:
        """execute() should delegate to run()."""
        job = _DummyJob(self._env)
        self.assertFalse(job.run_called)
        job.execute()
        self.assertTrue(job.run_called)

    def test_execute_catches_exception(self) -> None:
        """execute() should catch exceptions from run() and not propagate."""
        job = _DummyJob(self._env)
        job.run_should_fail = True
        # Should not raise
        job.execute()
        self.assertTrue(job.run_called)
