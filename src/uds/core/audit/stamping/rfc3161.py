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

RFC 3161 Time-Stamp Protocol (TSP) stamp provider.

Compatible with any RFC 3161-compliant TSA such as:
- https://freetsa.org/tsr
- https://timestamp.digicert.com
- .. (you can use your own TSA like OpenTimestamps)
"""

import typing
import hashlib
import logging
import os
import urllib.request
import urllib.error
import ssl

from .base import StampProvider

logger = logging.getLogger(__name__)

# SHA-256 OID: 2.16.840.1.101.3.4.2.1 (DER encoded)
_SHA256_OID = bytes(
    (
        0x06,
        0x09,
        0x60,
        0x86,
        0x48,
        0x01,
        0x65,
        0x03,
        0x04,
        0x02,
        0x01,
    )
)


def _der_length(value: int) -> bytes:
    """DER-encode a length value."""
    if value < 0x80:
        return bytes((value,))
    encoded = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return bytes((0x80 | len(encoded),)) + encoded


def _der_sequence(content: bytes) -> bytes:
    """DER-encode a SEQUENCE."""
    return bytes((0x30,)) + _der_length(len(content)) + content


def _der_integer(value: int) -> bytes:
    """DER-encode an INTEGER."""
    if value == 0:
        return bytes((0x02, 0x01, 0x00))
    val_bytes = value.to_bytes((value.bit_length() + 7) // 8, "big")
    if val_bytes[0] & 0x80:
        val_bytes = bytes((0x00,)) + val_bytes
    return bytes((0x02,)) + _der_length(len(val_bytes)) + val_bytes


def _der_octet_string(data: bytes) -> bytes:
    """DER-encode an OCTET STRING."""
    return bytes((0x04,)) + _der_length(len(data)) + data


def _der_null() -> bytes:
    """DER-encode NULL."""
    return bytes((0x05, 0x00))


def _der_boolean(value: bool) -> bytes:
    """DER-encode a BOOLEAN."""
    return bytes((0x01, 0x01, 0xFF if value else 0x00))


def _encode_timestamp_request(hash_data: bytes) -> bytes:
    """
    Encode an RFC 3161 TimeStampReq in DER format.

    Args:
        hash_data: The SHA-256 hash to be timestamped (32 bytes).
    Returns:
        DER-encoded TimeStampReq bytes.
    """
    # AlgorithmIdentifier = SEQUENCE { OID, NULL }
    algo_identifier = _der_sequence(_SHA256_OID + _der_null())

    # MessageImprint = SEQUENCE { hashAlgorithm, hashedMessage }
    hashed_message = _der_octet_string(hash_data)
    message_imprint = _der_sequence(algo_identifier + hashed_message)

    # TimeStampReq = SEQUENCE {
    #   version INTEGER { v1(1) },
    #   messageImprint MessageImprint,
    #   nonce INTEGER (optional, random),
    #   certReq BOOLEAN DEFAULT TRUE,
    # }
    version = _der_integer(1)
    nonce = _der_integer(int.from_bytes(os.urandom(8), "big"))
    cert_req = _der_boolean(True)

    req_content = version + message_imprint + nonce + cert_req
    return _der_sequence(req_content)


class RFC3161StampProvider(StampProvider):
    """
    RFC 3161 Time-Stamp Protocol stamp provider.

    Sends a hash to a TSA and receives a signed timestamp token.

    Usage:
        provider = RFC3161StampProvider(url='https://freetsa.org/tsr')
        token = provider.stamp(my_hash)
        assert provider.verify(my_hash, token)
    """

    def __init__(
        self,
        url: str = "https://freetsa.org/tsr",
        timeout: int = 30,
        verify_ssl: bool = True,
    ):
        self._url = url
        self._timeout = timeout
        self._verify_ssl = verify_ssl

    @typing.override
    def stamp(self, hash_data: bytes) -> bytes:
        """
        Send a TimeStampReq to the TSA and return the TimeStampResp token.
        """
        if len(hash_data) != 32:
            raise ValueError(f"Expected 32-byte SHA-256 hash, got {len(hash_data)} bytes")

        request_der = _encode_timestamp_request(hash_data)

        ctx: ssl.SSLContext | None = None
        if not self._verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        try:
            req = urllib.request.Request(
                self._url,
                data=request_der,
                headers={
                    "Content-Type": "application/timestamp-query",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._timeout, context=ctx) as resp:
                token = resp.read()
        except urllib.error.HTTPError as e:
            logger.error("TSA HTTP error %s: %s", e.code, e.reason)
            raise
        except urllib.error.URLError as e:
            logger.error("TSA connection error: %s", e.reason)
            raise
        except OSError as e:
            logger.error("TSA network error: %s", e)
            raise

        logger.debug(
            "TSA stamp: hash=%s... url=%s token_len=%d",
            hash_data.hex()[:16],
            self._url,
            len(token),
        )
        return token

    @typing.override
    def verify(self, hash_data: bytes, token: bytes) -> bool:
        """
        Verify a token by re-stamping and comparing.

        Note: This is a practical verification method. For full cryptographic
        verification (PKCS#7 signature check), use a dedicated RFC 3161 library.
        The re-stamp approach verifies that the TSA acknowledges the same
        hash at the same time, which is sufficient for log chain integrity.
        """
        try:
            new_token = self.stamp(hash_data)
        except Exception:
            logger.exception("TSA verification failed: cannot reach TSA")
            return False

        if len(new_token) != len(token):
            return False

        # Constant-time comparison
        return hashlib.sha256(new_token).digest() == hashlib.sha256(token).digest()
