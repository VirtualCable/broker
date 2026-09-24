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
Author: Andres Schumann, aschumann at virtualcable dot es

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
from uds.core.checks import Check, CheckOutcome
from uds.models import Log

# Thresholds for ``failed-logins-24h`` (in count of records).
# >50 / 24h is HIGH-fail; >10 is MEDIUM-fail; otherwise pass with the count.
_C1_HIGH_THRESHOLD: typing.Final[int] = 50
_C1_MEDIUM_THRESHOLD: typing.Final[int] = 10

# Threshold for ``internal-errors-24h`` (count of global ERROR records).
_C4_MEDIUM_THRESHOLD: typing.Final[int] = 50

# Threshold for ``brute-force-by-ip``: >= this many failed logins from a
# single source IP in the last 24h is HIGH-fail (likely credential stuffing).
_C2_PER_IP_THRESHOLD: typing.Final[int] = 20

# Common tail of the warnings logged by ``uds.core.jobs.scheduler`` and
# ``uds.core.jobs.delayed_task_runner`` when a row stamped by another broker
# (or DB node) carries a time ahead of the local one.
_CLOCK_SKEW_MARKER: typing.Final[str] = "being in the future"

# Same log line format as ``src/uds/reports/lists/failed_logins.py:51`` —
# kept duplicated here to avoid coupling this check module to the reports
# package. The message is built by ``uds.core.auths.auth.log_login`` at
# ``src/uds/core/auths/auth.py:532``.
LOGIN_RX: typing.Final[re.Pattern[str]] = re.compile(
    r"user (?P<user>.+?) has (?P<message>.+?) from (?P<ip>\S+) where os is (?P<os>.+)"
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


class FailedLogins24hCheck(Check):
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


class BruteForceByIpCheck(Check):
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


class TemporarilyBlockedLoginsCheck(Check):
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


class InternalErrors24hCheck(Check):
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


class ClockSkew24hCheck(Check):
    """Clock differences between UDS servers or database nodes in the last 24h."""

    id: typing.ClassVar[str] = "clock-skew-24h"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH

    @typing.override
    def run(self) -> CheckOutcome:
        count = Log.objects.filter(
            created__gte=_window(),
            owner_type=types.log.LogObjectType.SYSLOG,
            level__gte=types.log.LogLevel.WARNING,
            data__contains=_CLOCK_SKEW_MARKER,
        ).count()
        if count > 0:
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _(
                    "{count} scheduled tasks were found with execution times in the future in the last 24h:"
                    " the clocks of the UDS servers or database nodes are not in sync. Time-based controls"
                    " (session and token expiry, MFA codes, SAML validity windows) may misbehave."
                    " Enable NTP with the same time source on every server."
                ).format(count=count),
            )
        return (
            types.checks.CheckSeverity.INFO,
            True,
            _("No clock skew between UDS servers detected in the last 24h."),
        )
