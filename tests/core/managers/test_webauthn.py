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
Tests for the WebAuthn crypto module
(uds.core.managers.crypto.webauthn).
"""

import typing

from cryptography.hazmat.primitives.asymmetric import ec

from uds.core.managers.crypto.webauthn import (
    CborError,
    InvalidSignatureError,
    WebAuthnDataError,
    b64url_decode,
    b64url_encode,
    parse_cbor_complete,
    parse_cose_key,
    verify_assertion,
    verify_registration,
)
from uds.core.types.webauthn import WebAuthnResponse

from ...utils import webauthn as synthetic
from ...utils.test import UDSTestCase


class CborDecodeTest(UDSTestCase):
    """Tests the minimal CBOR decoder."""

    def test_uint(self) -> None:
        self.assertEqual(parse_cbor_complete(synthetic.cbor_int(0)), 0)
        self.assertEqual(parse_cbor_complete(synthetic.cbor_int(23)), 23)
        self.assertEqual(parse_cbor_complete(synthetic.cbor_int(24)), 24)
        self.assertEqual(parse_cbor_complete(synthetic.cbor_int(65535)), 65535)
        self.assertEqual(parse_cbor_complete(synthetic.cbor_int(1 << 32)), 1 << 32)

    def test_negative(self) -> None:
        self.assertEqual(parse_cbor_complete(synthetic.cbor_int(-7)), -7)
        self.assertEqual(parse_cbor_complete(synthetic.cbor_int(-257)), -257)

    def test_bytes_and_text(self) -> None:
        data = b"hello world"
        self.assertEqual(parse_cbor_complete(synthetic.cbor_bytes(data)), data)
        self.assertEqual(parse_cbor_complete(synthetic.cbor_text("hola")), "hola")

    def test_map_canonical(self) -> None:
        encoded = synthetic.cbor_map({1: 2, 3: -7, -1: 1, -2: b"\x01\x02", -3: b"\x03\x04"})
        decoded = parse_cbor_complete(encoded)
        self.assertEqual(decoded, {1: 2, 3: -7, -1: 1, -2: b"\x01\x02", -3: b"\x03\x04"})

    def test_trailing_garbage(self) -> None:
        with self.assertRaises(CborError):
            parse_cbor_complete(synthetic.cbor_int(1) + b"\xff")

    def test_truncated(self) -> None:
        with self.assertRaises(CborError):
            parse_cbor_complete(synthetic.cbor_bytes(b"abc")[:-2])


class B64urlTest(UDSTestCase):
    """Tests the base64url helpers."""

    def test_roundtrip(self) -> None:
        for data in (b"", b"\x00", b"abc", bytes(range(256))):
            self.assertEqual(b64url_decode(b64url_encode(data)), data)


class AuthenticatorDataTest(UDSTestCase):
    """authenticatorData parsing (via registration/assertion verification)."""

    @typing.override
    def setUp(self) -> None:
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.rp_id = "uds.example.com"
        self.origin = "https://uds.example.com"
        self.credential_id = b"\x01" * 32

    def _registration(self, challenge: bytes) -> dict[str, str]:
        return synthetic.registration_payload(
            self.private_key,
            self.rp_id,
            self.origin,
            challenge,
            credential_id=self.credential_id,
        )

    def test_registration_ok(self) -> None:
        challenge = b"C" * 32
        result = verify_registration(
            rp_id=self.rp_id,
            origin=self.origin,
            challenge=challenge,
            response=WebAuthnResponse(
                client_data_json=b64url_decode(self._registration(challenge)["client_data"]),
                attestation_object=b64url_decode(self._registration(challenge)["attestation_object"]),
            ),
        )
        self.assertEqual(result.credential_id, self.credential_id)
        self.assertTrue(result.user_verified)
        self.assertEqual(result.cose_key, synthetic.cose_key_ec2(self.private_key))

    def test_registration_challenge_mismatch(self) -> None:
        with self.assertRaises(WebAuthnDataError):
            verify_registration(
                rp_id=self.rp_id,
                origin=self.origin,
                challenge=b"W" * 32,
                response=WebAuthnResponse(
                    client_data_json=b64url_decode(self._registration(b"C" * 32)["client_data"]),
                    attestation_object=b64url_decode(self._registration(b"C" * 32)["attestation_object"]),
                ),
            )

    def test_registration_origin_mismatch(self) -> None:
        challenge = b"C" * 32
        # Same rp (hash is the same), wrong origin
        keys = self._registration(challenge)
        with self.assertRaises(WebAuthnDataError):
            verify_registration(
                rp_id=self.rp_id,
                origin="https://evil.example.com",
                challenge=challenge,
                response=WebAuthnResponse(
                    client_data_json=b64url_decode(keys["client_data"]),
                    attestation_object=b64url_decode(keys["attestation_object"]),
                ),
            )

    def test_registration_rp_hash_mismatch(self) -> None:
        challenge = b"C" * 32
        keys = self._registration(challenge)
        with self.assertRaises(WebAuthnDataError):
            verify_registration(
                rp_id="other-rp.example.com",
                origin=self.origin,
                challenge=challenge,
                response=WebAuthnResponse(
                    client_data_json=b64url_decode(keys["client_data"]),
                    attestation_object=b64url_decode(keys["attestation_object"]),
                ),
            )

    def test_assertion_ok(self) -> None:
        challenge = b"A" * 32
        keys = synthetic.assertion_payload(self.private_key, self.rp_id, self.origin, challenge, sign_count=7)
        new_count = verify_assertion(
            rp_id=self.rp_id,
            origin=self.origin,
            challenge=challenge,
            cose_key=synthetic.cose_key_ec2(self.private_key),
            previous_sign_count=0,
            response=WebAuthnResponse(
                client_data_json=b64url_decode(keys["client_data"]),
                authenticator_data=b64url_decode(keys["auth_data"]),
                signature=b64url_decode(keys["signature"]),
            ),
        )
        self.assertEqual(new_count, 7)

    def test_assertion_bad_signature(self) -> None:
        challenge = b"A" * 32
        keys = synthetic.assertion_payload(self.private_key, self.rp_id, self.origin, challenge, sign_count=7)
        other_key = ec.generate_private_key(ec.SECP256R1())
        replaced = synthetic.assertion_payload(other_key, self.rp_id, self.origin, challenge, sign_count=7)
        with self.assertRaises(InvalidSignatureError):
            verify_assertion(
                rp_id=self.rp_id,
                origin=self.origin,
                challenge=challenge,
                cose_key=synthetic.cose_key_ec2(self.private_key),
                previous_sign_count=0,
                response=WebAuthnResponse(
                    client_data_json=b64url_decode(keys["client_data"]),
                    authenticator_data=b64url_decode(keys["auth_data"]),
                    signature=b64url_decode(replaced["signature"]),
                ),
            )

    def test_assertion_replayed_challenge(self) -> None:
        challenge = b"A" * 32
        keys = synthetic.assertion_payload(self.private_key, self.rp_id, self.origin, challenge, sign_count=7)
        with self.assertRaises(WebAuthnDataError):
            verify_assertion(
                rp_id=self.rp_id,
                origin=self.origin,
                challenge=b"different" * 4,
                cose_key=synthetic.cose_key_ec2(self.private_key),
                previous_sign_count=0,
                response=WebAuthnResponse(
                    client_data_json=b64url_decode(keys["client_data"]),
                    authenticator_data=b64url_decode(keys["auth_data"]),
                    signature=b64url_decode(keys["signature"]),
                ),
            )

    def test_assertion_cloned_counter(self) -> None:
        challenge = b"A" * 32
        keys = synthetic.assertion_payload(self.private_key, self.rp_id, self.origin, challenge, sign_count=7)
        with self.assertRaises(WebAuthnDataError):
            verify_assertion(
                rp_id=self.rp_id,
                origin=self.origin,
                challenge=challenge,
                cose_key=synthetic.cose_key_ec2(self.private_key),
                previous_sign_count=10,
                response=WebAuthnResponse(
                    client_data_json=b64url_decode(keys["client_data"]),
                    authenticator_data=b64url_decode(keys["auth_data"]),
                    signature=b64url_decode(keys["signature"]),
                ),
            )

    def test_cose_parse_roundtrip(self) -> None:
        alg, public_key = parse_cose_key(synthetic.cose_key_ec2(self.private_key))
        self.assertEqual(alg, -7)
        numbers = typing.cast(ec.EllipticCurvePublicKey, public_key).public_numbers()
        self.assertEqual(numbers.x, self.private_key.public_key().public_numbers().x, "Wrong public key parsed")
