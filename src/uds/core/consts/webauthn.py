#
# Copyright (c) 2012-2026 Virtual Cable S.L.
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
WebAuthn related constants, used by the biometric login facility and the
WebAuthn crypto helper (uds.core.managers.crypto.webauthn).
"""

import typing

# Authenticator data flags (WebAuthn L3, section 6.1)
FLAG_USER_PRESENT: typing.Final[int] = 0x01
FLAG_USER_VERIFIED: typing.Final[int] = 0x04
FLAG_ATTESTED_CREDENTIAL_DATA: typing.Final[int] = 0x40
FLAG_EXTENSION_DATA: typing.Final[int] = 0x80

# rpIdHash (32) + flags (1) + signCount (4), minimum authenticatorData size
MIN_AUTH_DATA_LENGTH: typing.Final[int] = 37

# COSE key types (kty, RFC 9052)
COSE_KTY_OKP: typing.Final[int] = 1
COSE_KTY_EC2: typing.Final[int] = 2
COSE_KTY_RSA: typing.Final[int] = 3

# COSE algorithms accepted by UDS
COSE_ALG_ES256: typing.Final[int] = -7
COSE_ALG_EDDSA: typing.Final[int] = -8
COSE_ALG_RS256: typing.Final[int] = -257

# COSE elliptic curves (crv)
COSE_CRV_P256: typing.Final[int] = 1
COSE_CRV_P384: typing.Final[int] = 2
COSE_CRV_P521: typing.Final[int] = 3
COSE_CRV_ED25519: typing.Final[int] = 6

# clientDataJSON "type" values, see _parse_client_data
CLIENT_TYPE_CREATE: typing.Final[str] = "webauthn.create"
CLIENT_TYPE_GET: typing.Final[str] = "webauthn.get"

# Storage on user properties (uds.core.util.webauthn_credentials)
PROPERTY_PREFIX: typing.Final[str] = "webauthn-"

# Cryptographic sizes for our own endpoints (challenge and PRF salts)
CHALLENGE_SIZE: typing.Final[int] = 32
SALT_SIZE: typing.Final[int] = 32

# Session keys used by the biometric login views
SESSION_SAVE_DATA: typing.Final[str] = "webauthn_save"  # Pending registration
SESSION_UNLOCK_DATA: typing.Final[str] = "webauthn_unlock"  # Pending unlock (rotation data)

# Maximum age, in seconds, of a pending registration
PENDING_SAVE_MAX_AGE: typing.Final[int] = 120

# Maximum length of the random device id the browser sends on registration
DEVICE_ID_MAX_LENGTH: typing.Final[int] = 64
