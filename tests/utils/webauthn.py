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
Synthetic WebAuthn builders for tests.

Encodes, with a minimal CBOR encoder, the subset of WebAuthn structures the
UDS server parses and verifies:

* COSE ES256 public keys
* authenticatorData (with or without attested credential data)
* attestation objects (fmt "none")
* clientDataJSON
* Assertion signatures (DER ECDSA-P256 over authData || SHA256(clientDataJSON))
"""

import base64
import hashlib
import json
import typing

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec


def b64url_encode(data: bytes) -> str:
    """Base64url without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(data: str) -> bytes:
    """Base64url decode, accepting padded or unpadded input."""
    return base64.urlsafe_b64decode(data + "=" * ((4 - (len(data) % 4)) % 4))


# ---------------------------------------------------------------------------
# Minimal CBOR encoder (definite lengths, CTAP2 canonical compatible)
# ---------------------------------------------------------------------------


def _cbor_head(major: int, length: int) -> bytes:
    """Encodes the head byte(s) for a definite-length CBOR item."""
    def_head = (major << 5) | length
    if length < 24:
        return bytes([def_head])
    payload: bytes
    additional: int
    if length <= 0xFF:
        payload, additional = length.to_bytes(1, "big"), 24
    elif length <= 0xFFFF:
        payload, additional = length.to_bytes(2, "big"), 25
    elif length <= 0xFFFFFFFF:
        payload, additional = length.to_bytes(4, "big"), 26
    else:
        payload, additional = length.to_bytes(8, "big"), 27
    return bytes([(major << 5) | additional]) + payload


def cbor_int(value: int) -> bytes:
    """Encodes an unsigned or negative CBOR integer."""
    if value >= 0:
        return _cbor_head(0, value)
    return _cbor_head(1, -1 - value)


def cbor_bytes(data: bytes) -> bytes:
    """Encodes a CBOR byte string."""
    return _cbor_head(2, len(data)) + data


def cbor_text(text: str) -> bytes:
    """Encodes a CBOR text string."""
    encoded = text.encode("utf-8")
    return _cbor_head(3, len(encoded)) + encoded


def cbor_map(items: dict[typing.Any, typing.Any]) -> bytes:
    """Encodes a CBOR map, canonically (keys sorted by encoded key)."""
    encoded: list[tuple[bytes, bytes]] = []
    for key, value in items.items():
        encoded.append((cbor_encode(key), cbor_encode(value)))
    encoded.sort(key=lambda kv: kv[0])
    result = _cbor_head(5, len(encoded))
    for key_bytes, value_bytes in encoded:
        result += key_bytes + value_bytes
    return result


def cbor_encode(value: typing.Any) -> bytes:
    """Encodes any of the supported values (int, bytes, str, dict)."""
    if isinstance(value, bool):
        return bytes([0xF5]) if value else bytes([0xF4])
    if isinstance(value, int):
        return cbor_int(value)
    if isinstance(value, bytes):
        return cbor_bytes(value)
    if isinstance(value, str):
        return cbor_text(value)
    if isinstance(value, dict):
        typed_map: dict[typing.Any, typing.Any] = typing.cast("dict[typing.Any, typing.Any]", value)
        return cbor_map(typed_map)
    raise ValueError(f"Unsupported CBOR value type {type(value)}")


# ---------------------------------------------------------------------------
# WebAuthn structures
# ---------------------------------------------------------------------------

FLAG_USER_PRESENT = 0x01
FLAG_USER_VERIFIED = 0x04
FLAG_ATTESTED_CREDENTIAL_DATA = 0x40


def cose_key_ec2(private_key: ec.EllipticCurvePrivateKey) -> bytes:
    """CBOR-encoded COSE ES256 public key of the given private key."""
    public_numbers = private_key.public_key().public_numbers()
    coordinate_size = (private_key.curve.key_size + 7) // 8
    return cbor_map(
        {
            1: 2,  # kty: EC2
            3: -7,  # alg: ES256
            -1: 1,  # crv: P-256
            -2: public_numbers.x.to_bytes(coordinate_size, "big"),
            -3: public_numbers.y.to_bytes(coordinate_size, "big"),
        }
    )


def authenticator_data(
    rp_id: str,
    *,
    sign_count: int = 0,
    include_credential: bool = False,
    credential_id: bytes = b"",
    cose_key: bytes = b"",
    flags: int | None = None,
) -> bytes:
    """Builds an authenticatorData blob."""
    if flags is None:
        flags = FLAG_USER_PRESENT | FLAG_USER_VERIFIED
        if include_credential:
            flags |= FLAG_ATTESTED_CREDENTIAL_DATA
    data = hashlib.sha256(rp_id.encode("utf-8")).digest()
    data += bytes([flags])
    data += sign_count.to_bytes(4, "big")
    if include_credential:
        data += b"\x00" * 16  # aaguid
        data += len(credential_id).to_bytes(2, "big") + credential_id
        data += cose_key
    return data


def attestation_object(auth_data_bytes: bytes) -> bytes:
    """Builds an attestationObject (fmt "none") with the given authData."""
    return cbor_map({"fmt": "none", "attStmt": {}, "authData": auth_data_bytes})


def client_data_json(
    rp_type: str,
    challenge: bytes,
    origin: str,
) -> bytes:
    """Builds a clientDataJSON blob."""
    return json.dumps({"type": rp_type, "challenge": b64url_encode(challenge), "origin": origin}).encode(
        "utf-8"
    )


def registration_payload(
    private_key: ec.EllipticCurvePrivateKey,
    rp_id: str,
    origin: str,
    challenge: bytes,
    credential_id: bytes | None = None,
    sign_count: int = 0,
) -> dict[str, str]:
    """Builds a synthetic registration (create) response payload.

    Returns a dict with the b64url fields the views expect:
    client_data, attestation_object and vault.
    """
    if credential_id is None:
        credential_id = b"\x01" * 32
    cose_key = cose_key_ec2(private_key)
    auth_data_bytes = authenticator_data(
        rp_id, sign_count=sign_count, include_credential=True, credential_id=credential_id, cose_key=cose_key
    )
    client_data = client_data_json("webauthn.create", challenge, origin)
    vault = b64url_encode(json.dumps({"password": "test-password", "v": 1}).encode("utf-8"))
    return {
        "client_data": b64url_encode(client_data),
        "attestation_object": b64url_encode(attestation_object(auth_data_bytes)),
        "vault": vault,
    }


def assertion_payload(
    private_key: ec.EllipticCurvePrivateKey,
    rp_id: str,
    origin: str,
    challenge: bytes,
    sign_count: int,
) -> dict[str, str]:
    """Builds a synthetic authentication (get) response payload.

    Returns a dict with the b64url fields the views expect:
    client_data, auth_data and signature.
    """
    auth_data_bytes = authenticator_data(rp_id, sign_count=sign_count)
    client_data = client_data_json("webauthn.get", challenge, origin)
    signed = auth_data_bytes + hashlib.sha256(client_data).digest()
    signature = private_key.sign(signed, ec.ECDSA(hashes.SHA256()))
    return {
        "client_data": b64url_encode(client_data),
        "auth_data": b64url_encode(auth_data_bytes),
        "signature": b64url_encode(signature),
    }
