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

Dummy stamp provider for testing and development.
Always returns a deterministic token and always verifies as valid.
"""

import typing
import hashlib
import logging
import time

from .base import StampProvider

logger = logging.getLogger(__name__)


class DummyStampProvider(StampProvider):
    """
    No-op stamp provider.

    Returns a deterministic HMAC-style token for testing.
    Does NOT provide real external anchoring — only for development.
    """

    def __init__(self, secret: bytes = b"dummy-stamp-secret"):
        self._secret = secret

    @typing.override
    def stamp(self, hash_data: bytes) -> bytes:
        stamp_time = int(time.time()).to_bytes(8, "big")
        token = hashlib.sha256(self._secret + stamp_time + hash_data).digest()
        logger.debug("Dummy stamp: hash=%s token=%s", hash_data.hex(), token.hex())
        return stamp_time + token

    @typing.override
    def verify(self, hash_data: bytes, token: bytes) -> bool:
        if len(token) < 40:
            return False
        stamp_time = token[:8]
        expected = hashlib.sha256(self._secret + stamp_time + hash_data).digest()
        return token[8:] == expected
