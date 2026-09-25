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

Checks derived from runtime log/state.

The checks query ``uds.models.Log`` (the DB mirror of every ``UDSLogHandler``
record, ``uds.core.util.log.py:169``) instead of parsing log files directly,
which works on any worker and lets the same ``Log`` table drive the existing
``FailedLoginsReport``.
"""

import datetime
import re
import typing
from collections.abc import Iterator

from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from uds.core import types
from uds.core.checks import AutomaticCheck, CheckOutcome
from uds.models import Log

# Thresholds for ``failed-logins-24h`` (in count of records).
# >50 / 24h is HIGH-fail; >10 is MEDIUM-fail; otherwise pass with the count.
_C1_HIGH_THRESHOLD: typing.Final[int] = 50
_C1_MEDIUM_THRESHOLD: typing.Final[int] = 10

# Threshold for ``internal-errors-24h`` (count of global ERROR records).
_C4_MEDIUM_THRESHOLD: typing.Final[int] = 50

# Thresholds for ``clock-skew-24h`` (count of scheduler warnings about a job
# whose last_execution was in the future). Any occurrence already means some
# clock was out of sync; >= 20 in 24h means the skew is continuous.
_C5_LOW_THRESHOLD: typing.Final[int] = 1
_C5_HIGH_THRESHOLD: typing.Final[int] = 20

# Maximum affected job names listed in the clock-skew details
_MAX_EXAMPLES: typing.Final[int] = 5

# Threshold for ``brute-force-by-ip``: >= this many failed logins from a
# single source IP in the last 24h is HIGH-fail (likely credential stuffing).
_C2_PER_IP_THRESHOLD: typing.Final[int] = 20

# Same log line format as ``src/uds/reports/lists/failed_logins.py:51`` —
# kept duplicated here to avoid coupling this check module to the reports
# package. The message is built by ``uds.core.auths.auth.log_login`` at
# ``src/uds/core/auths/auth.py:532``.
LOGIN_RX: typing.Final[re.Pattern[str]] = re.compile(
    r"user (?P<user>.+?) has (?P<message>.+?) from (?P<ip>\S+) where os is (?P<os>.+)"
)

# Same line the scheduler writes (``uds.core.jobs.scheduler.execute_job``) when
# it picks up a job whose last_execution is in the future: a symptom of clock
# skew between broker nodes or database nodes.
CLOCK_SKEW_RX: typing.Final[re.Pattern[str]] = re.compile(
    r"Executed (?P<job>.+?) due to last_execution being in the future"
)


def window() -> datetime.datetime:
    """Returns ``now - 24h`` for the check windows."""
    return timezone.now() - datetime.timedelta(hours=24)


def _failed_login_rows() -> Iterator[str]:
    """Yields the ``data`` field of every failed-login log row in the last 24h."""
    qs = (
        Log.objects.filter(
            created__gte=window(),
            source=types.log.LogSource.WEB,
            owner_type=types.log.LogObjectType.AUTHENTICATOR,
            level__gte=types.log.LogLevel.ERROR,
        )
        .order_by()
        .values_list("data", flat=True)
    )
    yield from qs


class FailedLogins24hCheck(AutomaticCheck):
    """Volume of failed login attempts in the last 24h."""

    id: typing.ClassVar[str] = "failed-logins-24h"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY
    description: typing.ClassVar[str] = gettext_noop(
        "Counts the failed logins of the last 24 hours: from 10 it is a warning and from 50 it "
        "may be a brute force attack. The failed logins report shows the users and addresses "
        "involved."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        count = sum(1 for _ in _failed_login_rows())
        if count >= _C1_HIGH_THRESHOLD:
            return (
                types.checks.CheckSeverity.HIGH,
                False,
                _("{count} failed login attempts in the last 24h: a brute force may be in progress.").format(
                    count=count
                ),
            )
        if count >= _C1_MEDIUM_THRESHOLD:
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _("{count} failed login attempts in the last 24h.").format(count=count),
            )
        return (
            types.checks.CheckSeverity.INFO,
            True,
            _("{count} failed login attempts in the last 24h (within normal range).").format(count=count),
        )


class BruteForceByIpCheck(AutomaticCheck):
    """Brute-force patterns grouped by source IP."""

    id: typing.ClassVar[str] = "brute-force-by-ip"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY
    description: typing.ClassVar[str] = gettext_noop(
        "Groups the failed logins of the last 24 hours by source address and flags every address "
        "with 20 or more, a usual sign of password guessing. The details list each flagged "
        "address with its count; consider blocking them in the firewall."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        by_ip: dict[str, int] = {}
        for data in _failed_login_rows():
            m = LOGIN_RX.match(data or "")
            if not m:
                continue
            ip = m.group("ip")
            by_ip[ip] = by_ip.get(ip, 0) + 1
        flagged = [(ip, cnt) for ip, cnt in by_ip.items() if cnt >= _C2_PER_IP_THRESHOLD]
        if flagged:
            flagged.sort(key=lambda x: x[1], reverse=True)
            top = ", ".join(f"{ip}={cnt}" for ip, cnt in flagged[:5])
            return (
                types.checks.CheckSeverity.HIGH,
                False,
                _("Brute-force patterns detected by source IP in the last 24h: {top}.").format(top=top),
                [f"{ip}: {cnt}" for ip, cnt in flagged],
            )
        return (
            types.checks.CheckSeverity.INFO,
            True,
            _("No IP exceeds {threshold} failed login attempts in the last 24h.").format(
                threshold=_C2_PER_IP_THRESHOLD
            ),
        )


class TemporarilyBlockedLoginsCheck(AutomaticCheck):
    """Accounts temporarily blocked by the lockout in the last 24h."""

    id: typing.ClassVar[str] = "temporarily-blocked-logins"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY
    description: typing.ClassVar[str] = gettext_noop(
        "Counts the accounts temporarily blocked in the last 24 hours after too many wrong "
        "passwords. It can be an attack or a client retrying with an old password. The failed "
        "logins report shows the users and addresses involved."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        # The lockout path in ``src/uds/web/util/authentication.py:87`` logs
        # "Temporarily blocked" via ``log_login(..., as_error=True)``; that
        # lands in the Log table as an ERROR row on the authenticator with that
        # substring in ``data``.
        count = Log.objects.filter(
            created__gte=window(),
            source=types.log.LogSource.WEB,
            owner_type=types.log.LogObjectType.AUTHENTICATOR,
            level__gte=types.log.LogLevel.ERROR,
            data__contains="Temporarily blocked",
        ).count()
        if count > 0:
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _(
                    "{count} account(s) were temporarily blocked in the last 24h: an attacker may be"
                    " cycling credentials under the per-user lockout, or a misconfigured client is"
                    " retrying with stale credentials."
                ).format(count=count),
            )
        return (
            types.checks.CheckSeverity.INFO,
            True,
            _("No accounts were temporarily blocked in the last 24h."),
        )


class InternalErrors24hCheck(AutomaticCheck):
    """Global internal ERROR entries in the last 24h."""

    id: typing.ClassVar[str] = "internal-errors-24h"
    # Internal error rate is a system health signal, not an attack indicator
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH
    description: typing.ClassVar[str] = gettext_noop(
        "Counts the internal errors logged by the broker in the last 24 hours and warns from 50. "
        "A high number usually means a failing provider or service. Check uds.log, services.log "
        "and workers.log."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        # Global syslog entries (owner_id=0, owner_type=-1) at ERROR+ from the last
        # 24h; equivalent to grepping ``ERROR`` across uds.log/services.log/etc.
        qs = Log.objects.filter(
            created__gte=window(),
            owner_id=0,
            owner_type=-1,
            level__gte=types.log.LogLevel.ERROR,
        )
        count = qs.count()
        if count >= _C4_MEDIUM_THRESHOLD:
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _(
                    "{count} internal ERROR entries in the last 24h: check uds.log /"
                    " services.log / workers.log for failing providers or 5xx storms."
                ).format(count=count),
            )
        return (
            types.checks.CheckSeverity.INFO,
            True,
            _("{count} internal ERROR entries in the last 24h (within normal range).").format(count=count),
        )


class ClockSkew24hCheck(AutomaticCheck):
    """Scheduler warnings about a job with a future last_execution (clock skew)."""

    id: typing.ClassVar[str] = "clock-skew-24h"
    # Clock skew is a system health signal: scheduling uses the database clock,
    # so these warnings mean some clock (broker node or database node) drifted
    # and the scheduler had to compensate
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH
    description: typing.ClassVar[str] = gettext_noop(
        "Counts the scheduler warnings of jobs executed because their last_execution was in the future "
        "in the last 24 hours. It means some clock (a broker node or a database node) was out of sync. "
        "Any occurrence is worth investigating; the cluster checks (clock drift, timezones, db clock spread)"
        " help locate the source."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        # The scheduler writes this warning (uds.core.jobs.scheduler.execute_job)
        # whenever it picks up a job whose last_execution is ahead of the
        # database clock; UDSLogHandler mirrors it here as a global WARNING row
        qs = Log.objects.filter(
            created__gte=window(),
            owner_id=0,
            owner_type=-1,
            level=types.log.LogLevel.WARNING,
            data__icontains="last_execution being in the future",
        )
        count = qs.count()
        if count >= _C5_LOW_THRESHOLD:
            rows = list(qs.order_by("-created").values_list("data", flat=True)[:100])
            # Most recent unique affected job names, capped for the details
            jobs = list(
                dict.fromkeys(match.group("job") for row in rows if (match := CLOCK_SKEW_RX.search(row or "")))
            )[:_MAX_EXAMPLES]
            severity = (
                types.checks.CheckSeverity.HIGH
                if count >= _C5_HIGH_THRESHOLD
                else types.checks.CheckSeverity.LOW
            )
            return (
                severity,
                False,
                _(
                    "Scheduler executed {count} job(s) whose last_execution was in the future in the last 24h:"
                    " clocks were (or are) out of sync between broker or database nodes. Affected jobs: {jobs}."
                ).format(count=count, jobs=", ".join(jobs) if jobs else _("unknown")),
                jobs,
            )
        return (
            types.checks.CheckSeverity.INFO,
            True,
            _("No scheduler clock-skew warnings in the last 24h."),
        )
