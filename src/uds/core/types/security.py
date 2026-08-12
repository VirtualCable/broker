# -*- coding: utf-8 -*-

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

Security self-assessment types used by ``uds.core.util.security_checks`` and
exposed by the REST ``/system/security_check`` endpoint.
"""

import dataclasses
import enum
import typing


class SecurityCheckSeverity(str, enum.Enum):
    """
    Severity of a security check result, from most to least important.
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclasses.dataclass(frozen=True)
class SecurityCheckResult:
    """
    Result of a single security check.

    - ``id``: stable machine-readable identifier of the check.
    - ``severity``: importance of the check when it fails.
    - ``ok``: ``True`` when the check passes, ``False`` when the checked
      condition is a security concern.
    - ``message``: human readable detail, suitable for operator notification.
    """

    id: str
    severity: SecurityCheckSeverity
    ok: bool
    message: str

    def as_dict(self) -> dict[str, typing.Any]:
        return {
            "id": self.id,
            "severity": self.severity.value,
            "ok": self.ok,
            "message": self.message,
        }
