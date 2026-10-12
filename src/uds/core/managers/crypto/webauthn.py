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
WebAuthn helpers for UDS (subset of the WebAuthn L3 spec).

Notes: This module is intentionally free of Django/Uds models. It lives in the
crypto manager package because it is, at its core, a cryptography helper.

* Minimal, self-contained CBOR decoder (no external dependencies). Only
  definite-length encodings as emitted by WebAuthn authenticators (CTAP2
  canonical form) are supported, so it is NOT a general purpose CBOR decoder.
* ``authenticatorData`` parsing (rpIdHash, flags, sign counter, attested
  credential data and, structurally, extension data).
* COSE public key parsing: ES256 (ECDSA P-256), RS256 and EdDSA (Ed25519).
* Verification of registration (attestation) and authentication (assertion)
  responses: client data type/challenge/origin/crossOrigin, signature over
  ``authData || SHA256(clientDataJSON)``, rpIdHash, user presence/verification
  flags and sign counter monotonicity.
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import typing

from cryptography import exceptions as crypto_exceptions
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import ed25519 as asym_ed25519
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.types import PublicKeyTypes

from uds.core import consts
from uds.core import types

logger = logging.getLogger(__name__)


class WebAuthnError(Exception):
    """Base error for WebAuthn processing failures (malformed data, verification...)."""


class CborError(WebAuthnError):
    """Malformed CBOR input."""


class WebAuthnDataError(WebAuthnError):
    """Structure that does not follow the expected WebAuthn layout."""


class InvalidSignatureError(WebAuthnError):
    """Signature verification failed."""


def b64url_encode(data: bytes) -> str:
    """Base64url withouth padding, as used in WebAuthn wire formats."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(data: str) -> bytes:
    """Base64url decode, accepting padded or unpadded input."""
    remainder = len(data) % 4
    if remainder:
        data += "=" * (4 - remainder)
    try:
        return base64.urlsafe_b64decode(data)
    except (binascii.Error, ValueError) as e:
        raise WebAuthnDataError("Invalid base64url data") from e


def secure_compare(left: str, right: str) -> bool:
    """Constant-time string comparison."""
    return hmac.compare_digest(left.encode(), right.encode())


def has_user_presence(auth_data: types.webauthn.AuthenticatorData) -> bool:
    """True if the user presence flag is set."""
    return (auth_data.flags & consts.webauthn.FLAG_USER_PRESENT) != 0


def has_user_verification(auth_data: types.webauthn.AuthenticatorData) -> bool:
    """True if the user verification (usually fingerprint) flag is set."""
    return (auth_data.flags & consts.webauthn.FLAG_USER_VERIFIED) != 0


# ---------------------------------------------------------------------------
# Minimal CBOR decoder (definite lengths only, as emitted by authenticators)
# ---------------------------------------------------------------------------


def _read_uint(data: bytes, offset: int, length: int) -> tuple[int, int]:
    if offset + length > len(data):
        raise CborError("Truncated integer")
    return int.from_bytes(data[offset : offset + length], "big"), offset + length


def parse_cbor(data: bytes, offset: int = 0) -> tuple[typing.Any, int]:
    """Decodes one CBOR item from data (definite-length encodings only).

    Returns (value, next_offset). Tags are transparently removed. Floats and
    indefinite lengths are not supported.
    """
    if offset >= len(data):
        raise CborError("Empty CBOR data at offset")

    item = data[offset]
    major, additional = item >> 5, item & 0x1F
    offset += 1

    if additional < 24:
        length = additional
    elif additional == 24:
        length, offset = _read_uint(data, offset, 1)
    elif additional == 25:
        length, offset = _read_uint(data, offset, 2)
    elif additional == 26:
        length, offset = _read_uint(data, offset, 4)
    elif additional == 27:
        length, offset = _read_uint(data, offset, 8)
    else:  # 28-31: indefinite lengths and reserved values
        raise CborError("Indefinite length CBOR is not supported")

    if major == 0:  # unsigned integer
        return length, offset
    if major == 1:  # negative integer
        return -1 - length, offset
    if major == 2:  # byte string
        if offset + length > len(data):
            raise CborError("Truncated byte string")
        return data[offset : offset + length], offset + length
    if major == 3:  # text string
        if offset + length > len(data):
            raise CborError("Truncated text string")
        try:
            return data[offset : offset + length].decode("utf-8"), offset + length
        except UnicodeDecodeError as e:
            raise CborError("Invalid utf-8 text string") from e
    if major == 4:  # array
        result_array: list[typing.Any] = []
        for _ in range(length):
            value, offset = parse_cbor(data, offset)
            result_array.append(value)
        return result_array, offset
    if major == 5:  # map
        result_map: dict[typing.Any, typing.Any] = {}
        for _ in range(length):
            key, offset = parse_cbor(data, offset)
            item_value, offset = parse_cbor(data, offset)
            result_map[key] = item_value
        return result_map, offset
    if major == 6:  # tag -> transparent removal
        return parse_cbor(data, offset)
    if major == 7:  # simple values (only the ones WebAuthn emits)
        if additional == 20:
            return False, offset
        if additional == 21:
            return True, offset
        if additional == 22:
            return None, offset
        raise CborError("Unsupported simple value (floats are not supported)")

    raise CborError("Unsupported CBOR input")


def parse_cbor_complete(data: bytes) -> typing.Any:
    """Decodes a single CBOR item that must consume the whole input."""
    value, offset = parse_cbor(data, 0)
    if offset != len(data):
        raise CborError("Trailing data after CBOR item")
    return value


# ---------------------------------------------------------------------------
# COSE key handling
# ---------------------------------------------------------------------------


def parse_cose_key(cbor_key: bytes) -> tuple[int, PublicKeyTypes]:
    """Parses a CBOR-encoded COSE public key.

    Returns (cose_alg, cryptography public key). Raises WebAuthnDataError for
    unsupported key types/algorithms: only ES256, RS256 and EdDSA (Ed25519)
    are accepted.
    """
    decoded = parse_cbor_complete(cbor_key)
    if not isinstance(decoded, dict):
        raise WebAuthnDataError("COSE key is not a CBOR map")
    key_map = typing.cast("dict[typing.Any, typing.Any]", decoded)

    kty = key_map.get(1)
    alg = key_map.get(3)

    if kty == consts.webauthn.COSE_KTY_EC2 and alg in (consts.webauthn.COSE_ALG_ES256,):
        curve_id = key_map.get(-1)
        if not isinstance(curve_id, int):
            raise WebAuthnDataError("Malformed EC2 key")
        x = key_map.get(-2)
        y = key_map.get(-3)
        curves: dict[int, ec.EllipticCurve] = {
            consts.webauthn.COSE_CRV_P256: ec.SECP256R1(),
            consts.webauthn.COSE_CRV_P384: ec.SECP384R1(),
            consts.webauthn.COSE_CRV_P521: ec.SECP521R1(),
        }
        curve = curves.get(curve_id)
        if curve is None or not isinstance(x, bytes) or not isinstance(y, bytes):
            raise WebAuthnDataError("Malformed EC2 key")
        key = ec.EllipticCurvePublicNumbers(
            x=int.from_bytes(x, "big"), y=int.from_bytes(y, "big"), curve=curve
        ).public_key()
        return consts.webauthn.COSE_ALG_ES256, key

    if kty == consts.webauthn.COSE_KTY_RSA and alg == consts.webauthn.COSE_ALG_RS256:
        modulus = key_map.get(-1)
        exponent = key_map.get(-2)
        if not isinstance(modulus, bytes) or not isinstance(exponent, bytes):
            raise WebAuthnDataError("Malformed RSA key")
        key = rsa.RSAPublicNumbers(int.from_bytes(exponent, "big"), int.from_bytes(modulus, "big")).public_key()
        return consts.webauthn.COSE_ALG_RS256, key

    if kty == consts.webauthn.COSE_KTY_OKP and alg == consts.webauthn.COSE_ALG_EDDSA:
        if key_map.get(-2) != consts.webauthn.COSE_CRV_ED25519 or not isinstance(key_map.get(-1), bytes):
            raise WebAuthnDataError("Malformed OKP key")
        key = asym_ed25519.Ed25519PublicKey.from_public_bytes(typing.cast(bytes, key_map.get(-1)))
        return consts.webauthn.COSE_ALG_EDDSA, key

    raise WebAuthnDataError(f"Unsupported COSE key type/algorithm ({kty}/{alg})")


# ---------------------------------------------------------------------------
# Authenticator data parsing
# ---------------------------------------------------------------------------


def parse_authenticator_data(raw: bytes, expect_credential: bool = False) -> types.webauthn.AuthenticatorData:
    """Parses the WebAuthn authenticatorData structure.

    If ``expect_credential`` (registration responses), the attested credential
    data must be present. Otherwise (assertions) it must be absent.
    """
    if len(raw) < consts.webauthn.MIN_AUTH_DATA_LENGTH:
        raise WebAuthnDataError("authenticatorData too short")

    rp_id_hash = raw[:32]
    flags = raw[32]
    sign_count = int.from_bytes(raw[33:37], "big")
    offset = consts.webauthn.MIN_AUTH_DATA_LENGTH

    auth_data = types.webauthn.AuthenticatorData(rp_id_hash=rp_id_hash, flags=flags, sign_count=sign_count)

    if flags & consts.webauthn.FLAG_ATTESTED_CREDENTIAL_DATA:
        if offset + 18 > len(raw):
            raise WebAuthnDataError("Truncated attested credential data")
        auth_data.aaguid = raw[offset : offset + 16]
        credential_length = int.from_bytes(raw[offset + 16 : offset + 18], "big")
        offset += 18
        if offset + credential_length > len(raw):
            raise WebAuthnDataError("Truncated credential id")
        auth_data.credential_id = raw[offset : offset + credential_length]
        offset += credential_length
        key_start = offset
        try:
            cose_key, offset = parse_cbor(raw, offset)
        except CborError as e:
            raise WebAuthnDataError("Malformed credential public key") from e
        if not isinstance(cose_key, dict):
            raise WebAuthnDataError("Credential public key is not a CBOR map")
        auth_data.cose_key = raw[key_start:offset]

    if flags & consts.webauthn.FLAG_EXTENSION_DATA:
        # Extensions are parsed only to validate the structure; contents not used.
        try:
            _, offset = parse_cbor(raw, offset)
        except CborError as e:
            raise WebAuthnDataError("Malformed extension data") from e

    if expect_credential and not (flags & consts.webauthn.FLAG_ATTESTED_CREDENTIAL_DATA):
        raise WebAuthnDataError("Missing attested credential data")
    if not expect_credential and (flags & consts.webauthn.FLAG_ATTESTED_CREDENTIAL_DATA):
        raise WebAuthnDataError("Unexpected attested credential data in assertion")

    if offset != len(raw):
        raise WebAuthnDataError("Trailing garbage in authenticatorData")

    return auth_data


# ---------------------------------------------------------------------------
# Client data and signature verification
# ---------------------------------------------------------------------------


def _parse_client_data(
    client_data_json: bytes,
    *,
    expected_type: str,
    expected_challenge: bytes,
    expected_origin: str,
) -> dict[str, typing.Any]:
    """Validates clientDataJSON type, challenge and origin, returning parsed data."""
    try:
        client_data = json.loads(client_data_json.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise WebAuthnDataError("Invalid clientDataJSON") from e
    if not isinstance(client_data, dict):
        raise WebAuthnDataError("clientDataJSON is not an object")
    client_data_map = typing.cast("dict[str, typing.Any]", client_data)

    if client_data_map.get("type") != expected_type:
        raise WebAuthnDataError(f"Unexpected clientData type {client_data_map.get('type')!r}")

    provided_challenge = client_data_map.get("challenge", "")
    if not isinstance(provided_challenge, str) or not secure_compare(
        provided_challenge, b64url_encode(expected_challenge)
    ):
        raise WebAuthnDataError("Challenge does not match the issued one")

    if client_data_map.get("origin") != expected_origin:
        raise WebAuthnDataError(f"Origin mismatch: {client_data_map.get('origin')!r}")

    if client_data_map.get("crossOrigin"):
        raise WebAuthnDataError("Cross origin request is not allowed")

    return client_data_map


def _verify_signature(
    alg: int,
    public_key: PublicKeyTypes,
    signed_data: bytes,
    signature: bytes,
) -> None:
    """Verifies a WebAuthn signature with the algorithm matching the key."""
    try:
        if isinstance(public_key, ec.EllipticCurvePublicKey):
            if alg != consts.webauthn.COSE_ALG_ES256:
                raise WebAuthnDataError("Algorithm mismatch for EC key")
            public_key.verify(signature, signed_data, ec.ECDSA(hashes.SHA256()))
        elif isinstance(public_key, rsa.RSAPublicKey):
            if alg != consts.webauthn.COSE_ALG_RS256:
                raise WebAuthnDataError("Algorithm mismatch for RSA key")
            public_key.verify(signature, signed_data, asym_padding.PKCS1v15(), hashes.SHA256())
        elif isinstance(public_key, asym_ed25519.Ed25519PublicKey):
            if alg != consts.webauthn.COSE_ALG_EDDSA:
                raise WebAuthnDataError("Algorithm mismatch for Ed25519 key")
            public_key.verify(signature, signed_data)
        else:
            raise WebAuthnDataError("Unexpected public key type")
    except crypto_exceptions.InvalidSignature as e:
        raise InvalidSignatureError("Signature verification failed") from e


def _check_sign_count(previous: int, current: int) -> None:
    """Rejects assertions with a suspicious sign counter (cloned authenticator).

    Authenticators without a counter keep it at 0 forever, so the monotonicity
    check only applies when both counters are positive.
    """
    if previous and current and current <= previous:
        raise WebAuthnDataError("Sign counter did not increase, credential may be cloned")


def _verify_attestation_statement(
    fmt: str,
    statement: dict[str, typing.Any],
    auth_data_bytes: bytes,
    client_data_hash: bytes,
    cose_key: bytes,
) -> None:
    """Best-effort verification of the attestation statement.

    UDS does not enforce attestation policies, so no X.509 chain validation of
    attestation certificates is done. "none" is always accepted. For "packed"
    self attestation (no x5c), the signature is verified against the credential
    public key; failures are logged, not fatal.
    """
    if fmt == "none":
        return
    if not isinstance(statement, dict):
        return
    signature = statement.get("sig")
    alg = statement.get("alg")
    if fmt == "packed" and isinstance(signature, bytes) and isinstance(alg, int) and not statement.get("x5c"):
        try:
            _, public_key = parse_cose_key(cose_key)
            _verify_signature(alg, public_key, auth_data_bytes + client_data_hash, signature)
        except WebAuthnError as e:
            logger.warning("Could not verify packed self-attestation: %s", e)


# ---------------------------------------------------------------------------
# Public verification entry points
# ---------------------------------------------------------------------------


def verify_registration(
    *,
    rp_id: str,
    origin: str,
    challenge: bytes,
    response: types.webauthn.WebAuthnResponse,
) -> types.webauthn.VerifiedCredential:
    """Verifies a registration (navigator.credentials.create) response.

    Attestation formats accepted: "none" and "packed" (leaf only, see
    _verify_attestation_statement). Requires user verification flag and valid
    attested credential data.

    Returns VerifiedCredential, raises WebAuthnError on any failure.
    """
    _parse_client_data(
        response.client_data_json,
        expected_type=consts.webauthn.CLIENT_TYPE_CREATE,
        expected_challenge=challenge,
        expected_origin=origin,
    )

    decoded = parse_cbor_complete(response.attestation_object)
    if not isinstance(decoded, dict):
        raise WebAuthnDataError("attestationObject is not a CBOR map")
    attestation_map = typing.cast("dict[typing.Any, typing.Any]", decoded)
    fmt = attestation_map.get("fmt")
    if not isinstance(fmt, str):
        raise WebAuthnDataError("Missing fmt in attestationObject")
    auth_data_bytes = attestation_map.get("authData")
    if not isinstance(auth_data_bytes, bytes):
        raise WebAuthnDataError("Missing or malformed authData in attestationObject")
    statement: dict[typing.Any, typing.Any] = attestation_map.get("attStmt", {})
    if not isinstance(statement, dict):
        statement = {}
    if fmt not in ("none", "packed"):
        raise WebAuthnDataError(f"Unsupported attestation format {fmt!r}")

    auth_data = parse_authenticator_data(auth_data_bytes, expect_credential=True)
    if not has_user_verification(auth_data):
        raise WebAuthnDataError("User verification required")

    # Validate the stored public key can be used (raises on unsupported alg)
    parse_cose_key(auth_data.cose_key)

    _verify_attestation_statement(
        fmt, statement, auth_data_bytes, hashlib.sha256(response.client_data_json).digest(), auth_data.cose_key
    )

    if auth_data.rp_id_hash != hashlib.sha256(rp_id.encode("utf-8")).digest():
        raise WebAuthnDataError("rpId hash mismatch")

    return types.webauthn.VerifiedCredential(
        credential_id=auth_data.credential_id,
        cose_key=auth_data.cose_key,
        aaguid=auth_data.aaguid,
        sign_count=auth_data.sign_count,
        user_verified=has_user_verification(auth_data),
    )


def verify_assertion(
    *,
    rp_id: str,
    origin: str,
    challenge: bytes,
    cose_key: bytes,
    previous_sign_count: int,
    response: types.webauthn.WebAuthnResponse,
) -> int:
    """Verifies an authentication (navigator.credentials.get) response.

    Returns the new sign counter. Raises WebAuthnError on any failure.

    The caller is responsible of ensuring the credential used matches
    ``cose_key`` (and binding it to the expected user).
    """
    _parse_client_data(
        response.client_data_json,
        expected_type=consts.webauthn.CLIENT_TYPE_GET,
        expected_challenge=challenge,
        expected_origin=origin,
    )

    auth_data = parse_authenticator_data(response.authenticator_data, expect_credential=False)
    if not has_user_presence(auth_data):
        raise WebAuthnDataError("User presence required")
    if not has_user_verification(auth_data):
        raise WebAuthnDataError("User verification required")
    if auth_data.rp_id_hash != hashlib.sha256(rp_id.encode("utf-8")).digest():
        raise WebAuthnDataError("rpId hash mismatch")

    alg, public_key = parse_cose_key(cose_key)
    signed_data = response.authenticator_data + hashlib.sha256(response.client_data_json).digest()
    _verify_signature(alg, public_key, signed_data, response.signature)

    _check_sign_count(previous_sign_count, auth_data.sign_count)
    return auth_data.sign_count
