# -*- coding: utf-8 -*-
#
# Copyright (c) 2026 Virtual Cable S.L.U.
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
Content tests for the audit-log CSV report (ListReportAuditCSV).

Covers the gap left by the smoke tests: both branches of gen_data() are
exercised — a REST access row ("ip [user]: [method/code] request") and a
log_audit action row ("ip [user]: request", no method/code) — and the CSV
output is parsed and asserted, including the 'Level' column and date-range
filtering.
"""

import csv
import datetime
import io
import time

from django.utils import timezone

from uds.models import Log
from uds.core import types
from uds.core.util import log as log_util
from uds.reports.lists.audit import ListReportAuditCSV

from ...utils.test import UDSTransactionTestCase

RANGE_START = datetime.date(2024, 1, 1)
RANGE_END = datetime.date(2024, 1, 31)
_STAMP = timezone.make_aware(datetime.datetime(2024, 1, 15, 12, 0, 0))


class AuditReportTest(UDSTransactionTestCase):
    def _seed(self, data: str, level: 'log_util.LogLevel') -> None:
        Log.objects.create(
            owner_id=0,
            owner_type=int(types.log.LogObjectType.SYSLOG),
            created=_STAMP,
            source=types.log.LogSource.REST,
            level=int(level),
            data=data,
        )

    def _generate_rows(self) -> list[list[str]]:
        report = ListReportAuditCSV()
        report.start_date.value = RANGE_START
        report.end_date.value = RANGE_END
        out = report.generate()
        return list(csv.reader(io.StringIO(out.decode())))

    def test_report_renders_both_row_kinds(self) -> None:
        # REST access row: has [method/code]
        self._seed('10.0.0.1 [admin]: [GET/200] /uds/rest/providers', log_util.LogLevel.INFO)
        # log_audit action row: no [method/code]
        self._seed('10.0.0.2 [operator]: created provider "vmware-01"', log_util.LogLevel.WARNING)

        rows = self._generate_rows()

        # Header (7 columns, includes Level)
        header = rows[0]
        self.assertEqual(len(header), 7)
        self.assertIn('Level', header)
        self.assertIn('Method', header)

        body = rows[1:]
        self.assertEqual(len(body), 2)

        by_ip = {r[2]: r for r in body}

        # REST row: method GET, response code decoded, request preserved
        rest = by_ip['10.0.0.1']
        self.assertEqual(rest[1], 'INFO')          # Level
        self.assertEqual(rest[3], 'admin')          # User
        self.assertEqual(rest[4], 'GET')            # Method
        self.assertEqual(rest[5], '200/OK')         # Response code decoded
        self.assertEqual(rest[6], '/uds/rest/providers')

        # AUDIT row: synthetic 'AUDIT' method, empty response code
        audit = by_ip['10.0.0.2']
        self.assertEqual(audit[1], 'WARNING')       # Level
        self.assertEqual(audit[3], 'operator')      # User
        self.assertEqual(audit[4], 'AUDIT')         # Method
        self.assertEqual(audit[5], '')              # No response code
        self.assertEqual(audit[6], 'created provider "vmware-01"')

    def test_pathological_data_parses_linearly(self) -> None:
        # Crafted request path (spaces + '[') used to make the old regexes backtrack polynomially
        self._seed('10.0.0.3 [admin]: [GET/200] /uds/rest/' + '[ ' * 4096, log_util.LogLevel.INFO)

        start = time.monotonic()
        body = self._generate_rows()[1:]
        elapsed = time.monotonic() - start

        self.assertEqual(len(body), 1)
        self.assertEqual(body[0][4], 'GET')
        self.assertLess(elapsed, 5.0)  # Guards against reintroducing a backtracking parser

    def test_rows_outside_date_range_excluded(self) -> None:
        self._seed('10.0.0.9 [admin]: [GET/200] /in-range', log_util.LogLevel.INFO)
        # Out of range
        Log.objects.create(
            owner_id=0,
            owner_type=int(types.log.LogObjectType.SYSLOG),
            created=timezone.make_aware(datetime.datetime(2023, 12, 31, 12, 0, 0)),
            source=types.log.LogSource.REST,
            level=int(log_util.LogLevel.INFO),
            data='10.0.0.8 [admin]: [GET/200] /out-of-range',
        )

        body = self._generate_rows()[1:]
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0][6], '/in-range')
