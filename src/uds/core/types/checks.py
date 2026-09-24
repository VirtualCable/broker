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

Self-assessment check types used by ``uds.core.checks`` and exposed by the REST
``/system/checks`` endpoint.

A check evaluates a single condition of the installation (weak settings, stuck
queues, ...) and reports its severity, whether it passes and a human readable
detail. The category tells which area the condition belongs to.
"""

import dataclasses
import enum
import typing


class CheckSeverity(str, enum.Enum):
    """
    Severity of a check result, from most to least important.
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class CheckCategory(str, enum.Enum):
    """
    Area a check belongs to.

    - ``SECURITY``: configuration or state weaknesses an attacker could use.
    - ``HEALTH``: operational indicators (queues, stuck elements, ...) that
      may degrade the system but are not exploitable by themselves.
    """

    SECURITY = "security"
    HEALTH = "health"


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """
    Result of a single check.

    - ``id``: stable machine-readable identifier of the check.
    - ``severity``: importance of the check when it fails.
    - ``ok``: ``True`` when the check passes, ``False`` when the checked
      condition deserves attention.
    - ``message``: human readable detail, suitable for operator notification.
    - ``category``: area the check belongs to.
    """

    id: str
    severity: CheckSeverity
    ok: bool
    message: str
    category: CheckCategory

    def as_dict(self) -> dict[str, typing.Any]:
        return {
            "id": self.id,
            "severity": self.severity.value,
            "ok": self.ok,
            "message": self.message,
            "category": self.category.value,
        }
