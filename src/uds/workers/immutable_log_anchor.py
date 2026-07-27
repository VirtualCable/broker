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

Worker that periodically inserts re-anchor entries into the immutable
audit log.  Controlled by ``GlobalConfig.IMMUTABLE_LOG_REANCHOR`` (seconds).
"""

import logging
import typing

from uds.core.audit.immutable import ImmutableLogger
from uds.core.jobs import Job
from uds.core.util.config import GlobalConfig
from uds.models.immutable_log import ImmutableLog

logger = logging.getLogger(__name__)


class ImmutableLogAnchorJob(Job):
    friendly_name = "Immutable Log Anchor"

    @typing.override
    def next_execution_delay(self) -> int:
        if not ImmutableLogger.is_enabled():
            return 600  # disabled → check every 10 min
        interval = GlobalConfig.IMMUTABLE_LOG_REANCHOR.as_int()
        if interval <= 0:
            return 600
        return min(max(interval, 120), 60 * 60 * 24)  # between 2 min and 24 h

    @typing.override
    def run(self) -> None:
        if not ImmutableLogger.is_enabled():
            return
        interval = GlobalConfig.IMMUTABLE_LOG_REANCHOR.as_int()
        if interval <= 0:
            return
        if not ImmutableLog.objects.exists():
            return
        last = ImmutableLog.objects.latest()
        ImmutableLogger.create_anchor(bytes(last.entry_hash))  # pyrefly: ignore[unnecessary-type-conversion]
