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

import typing

# Maximum pending flows an user (normally an AI agent acting on their behalf)
# can have at once. Creating a new flow beyond this limit is refused.
MAX_FLOWS_PER_USER: typing.Final[int] = 20

# Maximum actions inside a single flow. Keeps proposals small enough to be
# reviewed (and approved) as a whole.
MAX_ACTIONS_PER_FLOW: typing.Final[int] = 8

# Days a flow stays valid after creation. Pending flows older than this are
# expired by the application layer. Used when the proposer does not declare
# its own expected resolution window.
FLOW_TTL_DAYS: typing.Final[int] = 7

# Bounds (hours) of the proposer-declared expiration window
# (``expires_in_hours``): proposals carry the expected time an
# administrator will need to resolve them, clamped to sane values.
MIN_TTL_HOURS: typing.Final[int] = 1
MAX_TTL_HOURS: typing.Final[int] = FLOW_TTL_DAYS * 24  # 30 days

# Days an expired flow can still be reopened (edited back to pending) by
# its proposer. Past this grace window the proposal is final: a new one
# must be proposed.
REOPEN_GRACE_DAYS: typing.Final[int] = 7

# Days of own-flow history the proposal tools surface to the agent.
# Older flows remain visible to administrators (and in the database),
# they just disappear from the agent's view to keep its context bounded.
VISIBILITY_DAYS: typing.Final[int] = 30
