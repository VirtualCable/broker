#
# Copyright (c) 2025 Virtual Cable S.L.
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
"""

import enum


class FlowStatus(enum.StrEnum):
    """Status of an ActionFlow (a proposed sequence of changes)."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    EXECUTING = "executing"
    EXECUTED = "executed"
    FAILED = "failed"

    @staticmethod
    def as_choices() -> tuple[tuple[str, str], ...]:
        return tuple((f.value, f.value.capitalize()) for f in FlowStatus)

    @staticmethod
    def from_str(value: str) -> "FlowStatus":
        try:
            return FlowStatus[value.upper()]
        except KeyError:
            return FlowStatus.PENDING


class FlowActionStatus(enum.StrEnum):
    """Status of a single action inside an ActionFlow."""

    PENDING = "pending"
    APPROVED = "approved"
    EXECUTING = "executing"
    EXECUTED = "executed"
    FAILED = "failed"
    SKIPPED = "skipped"

    @staticmethod
    def as_choices() -> tuple[tuple[str, str], ...]:
        return tuple((f.value, f.value.capitalize()) for f in FlowActionStatus)

    @staticmethod
    def from_str(value: str) -> "FlowActionStatus":
        try:
            return FlowActionStatus[value.upper()]
        except KeyError:
            return FlowActionStatus.PENDING
