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
# CAUSED AND ON THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""
Constants for the usage prediction subsystem.

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import typing

# Number of weeks of historical data used by default to build profiles
TRAINING_WEEKS: typing.Final[int] = 8

# Minimum samples per (day-of-week, hour) cell to consider it reliable
MIN_SAMPLES_PER_CELL: typing.Final[int] = 5

# Relative deviation above which the recent pattern is considered "broken"
# (holidays, policy change, outage). Used by callers to compare against the
# float returned by predictor.detect_anomaly().
ANOMALY_THRESHOLD: typing.Final[float] = 0.5

# Linear recency bias for weighted mean (0 = uniform, 1 = last sample weighs 2x)
RECENCY_BIAS: typing.Final[float] = 0.5

# Number of (day-of-week, hour) cells in a week
CELLS_IN_WEEK: typing.Final[int] = 7 * 24

# Cache owner and TTL for get_profile() cached profiles
PROFILE_CACHE_OWNER: typing.Final[str] = "uds.predictor"
PROFILE_CACHE_TIMEOUT: typing.Final[int] = 30 * 24 * 3600  # 30 days
