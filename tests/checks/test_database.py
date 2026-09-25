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
Author: Andres Schumann, aschumann at virtualcable dot es

Tests for the database time check (uds.checks.database).
"""

import datetime
from unittest import mock

from django.utils import timezone

from uds.checks import database as database_module
from uds.core import types
from uds.core.checks import runner as runner_module

from ..utils.test import UDSTransactionTestCase


class DbAppTimeMismatchCheckTest(UDSTransactionTestCase):
    def _run_check(self) -> types.checks.CheckResult:
        results = {result.id: result for result in runner_module.run_checks(types.checks.CheckKind.AUTOMATIC)}
        return results["db-app-time-mismatch"]

    def test_passes_when_times_agree(self) -> None:
        self.assertTrue(self._run_check().ok)

    def test_fails_with_a_time_zone_offset(self) -> None:
        shifted = timezone.now() + datetime.timedelta(hours=2)
        with mock.patch.object(database_module, "sql_now", return_value=shifted):
            result = self._run_check()
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.checks.CheckSeverity.HIGH)
        self.assertIn("7200", result.message)
