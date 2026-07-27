# -*- coding: utf-8 -*-
#
# Copyright (c) 2012-2025 Virtual Cable S.L.U.
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

Abstract base class for stamp providers.
"""

import abc


class StampProvider(abc.ABC):
    """
    A stamp provider obtains a trusted external timestamp/signature
    for a given piece of data (a hash), and can later verify that
    the returned token is valid for that data.

    Used to anchor the genesis block of the immutable log to an
    external, tamper-proof source of truth (e.g. RFC 3161 TSA,
    OpenTimestamps, blockchain anchoring).
    """

    @abc.abstractmethod
    def stamp(self, hash_data: bytes) -> bytes:
        """
        Obtain a trusted timestamp/signature token for ``hash_data``.

        Returns the raw token bytes (e.g. DER-encoded TimeStampResp for RFC 3161).
        """

    @abc.abstractmethod
    def verify(self, hash_data: bytes, token: bytes) -> bool:
        """
        Verify that ``token`` is a valid timestamp/signature for ``hash_data``.

        Returns True if the token is valid and was produced by this provider.
        """
