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

Tests for the ImmutableLogAnchorJob worker.
"""

import unittest.mock

from uds.core.environment import Environment
from uds.workers.immutable_log_anchor import ImmutableLogAnchorJob

from ...utils.test import UDSTestCase


class ImmutableLogAnchorJobTest(UDSTestCase):
    """Tests for the anchor worker."""

    def setUp(self) -> None:
        super().setUp()
        self._env = Environment.testing_environment()

    def test_next_delay_disabled_returns_10min(self) -> None:
        with unittest.mock.patch("uds.workers.immutable_log_anchor.ImmutableLogger.is_enabled", return_value=False):
            job = ImmutableLogAnchorJob(self._env)
            self.assertEqual(job.next_execution_delay(), 600)

    def test_next_delay_interval_zero_returns_10min(self) -> None:
        with (
            unittest.mock.patch("uds.workers.immutable_log_anchor.ImmutableLogger.is_enabled", return_value=True),
            unittest.mock.patch("uds.workers.immutable_log_anchor.GlobalConfig") as mock_cfg,
        ):
            mock_cfg.IMMUTABLE_LOG_REANCHOR.as_int.return_value = 0
            job = ImmutableLogAnchorJob(self._env)
            self.assertEqual(job.next_execution_delay(), 600)

    def test_next_delay_minimum_120s(self) -> None:
        with (
            unittest.mock.patch("uds.workers.immutable_log_anchor.ImmutableLogger.is_enabled", return_value=True),
            unittest.mock.patch("uds.workers.immutable_log_anchor.GlobalConfig") as mock_cfg,
        ):
            mock_cfg.IMMUTABLE_LOG_REANCHOR.as_int.return_value = 30
            job = ImmutableLogAnchorJob(self._env)
            self.assertEqual(job.next_execution_delay(), 120)  # clamped to min 120

    def test_next_delay_uses_config_value(self) -> None:
        with (
            unittest.mock.patch("uds.workers.immutable_log_anchor.ImmutableLogger.is_enabled", return_value=True),
            unittest.mock.patch("uds.workers.immutable_log_anchor.GlobalConfig") as mock_cfg,
        ):
            mock_cfg.IMMUTABLE_LOG_REANCHOR.as_int.return_value = 3600
            job = ImmutableLogAnchorJob(self._env)
            self.assertEqual(job.next_execution_delay(), 3600)

    def test_run_does_nothing_when_disabled(self) -> None:
        with unittest.mock.patch("uds.workers.immutable_log_anchor.ImmutableLogger.is_enabled", return_value=False):
            job = ImmutableLogAnchorJob(self._env)
            # Should not raise, should not call create_anchor
            job.run()

    def test_run_does_nothing_when_interval_zero(self) -> None:
        with (
            unittest.mock.patch("uds.workers.immutable_log_anchor.ImmutableLogger.is_enabled", return_value=True),
            unittest.mock.patch("uds.workers.immutable_log_anchor.GlobalConfig") as mock_cfg,
        ):
            mock_cfg.IMMUTABLE_LOG_REANCHOR.as_int.return_value = 0
            job = ImmutableLogAnchorJob(self._env)
            job.run()

    def test_run_does_nothing_when_empty_chain(self) -> None:
        with (
            unittest.mock.patch("uds.workers.immutable_log_anchor.ImmutableLogger.is_enabled", return_value=True),
            unittest.mock.patch("uds.workers.immutable_log_anchor.GlobalConfig") as mock_cfg,
        ):
            mock_cfg.IMMUTABLE_LOG_REANCHOR.as_int.return_value = 3600
            job = ImmutableLogAnchorJob(self._env)
            # Chain is empty → no crash
            job.run()

    def test_appends_anchors_even_when_last_is_anchor(self) -> None:
        import hashlib

        from uds.models.immutable_log import ImmutableLog

        ImmutableLog.objects.create(
            sequence=1,
            anchor=True,
            stamp="2025-01-01T00:00:00Z",
            previous_hash=b"\x00" * 32,
            data=b"x",
            entry_hash=hashlib.sha256(b"x").digest(),
        )

        with (
            unittest.mock.patch("uds.workers.immutable_log_anchor.ImmutableLogger.is_enabled", return_value=True),
            unittest.mock.patch("uds.workers.immutable_log_anchor.GlobalConfig") as mock_cfg,
        ):
            mock_cfg.IMMUTABLE_LOG_REANCHOR.as_int.return_value = 3600
            job = ImmutableLogAnchorJob(self._env)
            job.run()
            # Still 1 entry (no new anchor)
            self.assertEqual(ImmutableLog.objects.count(), 2)
