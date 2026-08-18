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

Constants used by the security self-assessment checks and related code.
"""

import hashlib
import typing

# Shipped default of ``GlobalConfig.SUPER_USER_PASS`` (see uds.core.util.config).
# Detects installations where the root password has never been rotated.
DEFAULT_SUPERUSER_PASSWORD: typing.Final[str] = "udsmam0"

# Shipped default of ``settings.SECRET_KEY`` (``src/server/settings.py.sample:183``).
# OSS installs that copied the sample verbatim are running with this key, which
# means any attacker who read the public source can forge session tokens.
DEFAULT_SECRET_KEY: typing.Final[str] = "s5ky!7b5f#s35!e38xv%e-+iey6yi-#630x)kk3kk5_j8rie2*"

# SHA256 of the shipped default of ``settings.RSA_KEY`` (``settings.py.sample:188``).
# We hash because the key is a multi-line PEM literal that is awkward to keep as a
# constant; comparing fingerprints is enough to flag an install that never
# rotated the key.
DEFAULT_RSA_KEY_SHA256: typing.Final[str] = "f8a73d4bb154a710bf235ac1fbbd6bf5b93774284358478f622492b941b19528"


def rsa_key_fingerprint(key: str) -> str:
    """Returns the SHA256 fingerprint of an RSA key PEM for comparison purposes."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
