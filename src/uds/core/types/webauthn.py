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
WebAuthn data types, used by the biometric login facility.

* ``AuthenticatorData``, ``WebAuthnResponse`` and ``VerifiedCredential`` are
  the wire/parsing types handled by the WebAuthn crypto helper
  (``uds.core.managers.crypto.webauthn``).
* ``StoredCredential`` is the serializable user level value object, kept as
  one encrypted user property per credential (see
  ``uds.core.util.webauthn_credentials``).
"""

import dataclasses
import typing


@dataclasses.dataclass(slots=True)
class AuthenticatorData:
    """Parsed authenticator data (WebAuthn L3, section 6.1)."""

    rp_id_hash: bytes
    flags: int
    sign_count: int
    # Present only in registration responses (attested credential data)
    aaguid: bytes = b""
    credential_id: bytes = b""
    # Raw COSE key, as CBOR emitted by the authenticator
    cose_key: bytes = b""


@dataclasses.dataclass(slots=True)
class WebAuthnResponse:
    """Fields sent back by the client for verification.

    For registrations, ``attestation_object`` is used; for assertions,
    ``authenticator_data`` + ``signature`` are used.
    """

    client_data_json: bytes
    authenticator_data: bytes = b""
    signature: bytes = b""
    attestation_object: bytes = b""


@dataclasses.dataclass(slots=True)
class VerifiedCredential:
    """Result of a successful registration verification."""

    credential_id: bytes
    cose_key: bytes
    aaguid: bytes
    sign_count: int
    user_verified: bool


@dataclasses.dataclass
class StoredCredential:
    """
    A WebAuthn credential registered by a user for biometric login.

    Fully serializable to/from JSON (persisted as one encrypted user
    property, see ``uds.core.util.webauthn_credentials``).
    """

    credential_id: str  # base64url
    rp_id: str
    user_handle: str  # base64url
    aaguid: str
    public_key: str  # base64url CBOR COSE key
    sign_count: int
    prf_salt: str  # base64url, active PRF salt (rotated on every unlock)
    vault: str  # opaque client-encrypted blob (base64url)
    generation: int
    created: float  # epoch seconds
    last_used: float | None  # epoch seconds
    enabled: bool = True
    # Random id kept by the browser that registered the credential. Credentials
    # registered before it existed have it empty.
    device_id: str = ""

    def to_dict(self) -> dict[str, typing.Any]:
        """Serializes to a JSON-compatible dict (property value)."""
        return dict(self.__dict__)  # asdict() is equivalent here

    @classmethod
    def from_dict(cls, data: dict[str, typing.Any]) -> "StoredCredential | None":
        """Rebuilds from a serialized dict. Returns None if malformed."""
        try:
            return cls(
                credential_id=str(data["credential_id"]),
                rp_id=str(data["rp_id"]),
                user_handle=str(data["user_handle"]),
                aaguid=str(data.get("aaguid", "")),
                public_key=str(data["public_key"]),
                sign_count=int(data.get("sign_count", 0)),
                prf_salt=str(data["prf_salt"]),
                vault=str(data.get("vault", "")),
                generation=int(data.get("generation", 0)),
                created=float(data.get("created", 0.0)),
                last_used=(float(data["last_used"]) if data.get("last_used") is not None else None),
                enabled=bool(data.get("enabled", True)),
                device_id=str(data.get("device_id", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None
