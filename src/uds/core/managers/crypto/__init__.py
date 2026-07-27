# -*- coding: utf-8 -*-
#
# Copyright (c) 2012-2023 Virtual Cable S.L.
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

import array
import base64
import codecs
import collections.abc
import hashlib
import json
import logging
import pathlib
import re
import secrets
import string
import struct
import typing
import uuid


# For password secrets
from argon2 import PasswordHasher
from argon2 import Type as ArgonType

# Standard cryptography library
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher
from cryptography.hazmat.primitives.ciphers import aead
from cryptography.hazmat.primitives.ciphers import algorithms
from cryptography.hazmat.primitives.ciphers import modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.utils import timezone

from uds.core import types
from uds.core.util import singleton

from . import certs
from . import kem
from . import rdp

uuid7: None | collections.abc.Callable[[], "uuid.UUID"]

try:
    from edwh_uuid7 import uuid7  # type: ignore
except ImportError:
    uuid7 = None


logger = logging.getLogger(__name__)

if typing.TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.dh import DHPrivateKey
    from cryptography.hazmat.primitives.asymmetric.dsa import DSAPrivateKey
    from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

# Note the REAL BIG importance of the SECRET_KEY. if lost, all encripted stored data (almost all fields) will be lost...
UDSK: typing.Final[bytes] = settings.SECRET_KEY[
    8:24
].encode()  # UDS key, new, for AES256, so it's 16 bytes length


class CryptoManager(metaclass=singleton.Singleton):
    _rsa: "RSAPrivateKey"
    _namespace: uuid.UUID

    def __init__(self) -> None:
        self._rsa = typing.cast(
            "RSAPrivateKey",
            serialization.load_pem_private_key(
                settings.RSA_KEY.encode(), password=None, backend=default_backend()
            ),
        )
        self._namespace = uuid.UUID("627a37a5-e8db-431a-b783-73f7d20b4934")

    @staticmethod
    def manager() -> "CryptoManager":
        return CryptoManager()  # Singleton pattern will return always the same instance

    @staticmethod
    def ensure_aes_key(key: str | bytes, length: int) -> bytes:
        """
        Generate an AES key of the specified length using the provided key.

        This method is used to generate an AES key of the specified length using the provided key.

        Args:
            key (Union[str, bytes]): The key used to generate the AES key. It can be either a string or bytes.
            length (int): The desired length of the AES key.

        Returns:
            bytes: The generated AES key.

        Raises:
            ValueError: If the length is not a positive integer.

        """
        key = key.encode() if isinstance(key, str) else key

        # If we already have a length key, just return it, for compat with old data, but if not, derive a new one
        if len(key) == length:
            return key

        return CryptoManager.manager().derive_password(key, b"uds-aes-key", length=length)

    def encrypt(self, value: str) -> str:
        return codecs.encode(
            self._rsa.public_key().encrypt(
                value.encode(),
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            ),
            "base64",
        ).decode()

    def decrypt(self, value: str) -> str:
        data: bytes = codecs.decode(value.encode(), "base64")

        try:
            # First, try new "cryptography" decrpypting
            decrypted: bytes = self._rsa.decrypt(
                data,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            )
        except Exception:  # Old method is not supported
            logger.exception("Decripting value failed")
            return "decript error"
        return decrypted.decode()

    def aes256_cbc_encrypt(self, text: bytes, key: bytes, base64: bool = False) -> bytes:
        # First, match key to 16 bytes. If key is over 16, create a new one based on key of 16 bytes length
        cipher = Cipher(
            algorithms.AES(CryptoManager.ensure_aes_key(key, 16)),
            modes.CBC(b"udsinitvectoruds"),
            backend=default_backend(),
        )
        rnd_string = secrets.token_bytes(16)  # Same as block size of CBC (that is 16 here)
        padded_length = ((len(text) + 4 + 15) // 16) * 16  # calculate padding length, 4 is for length of text
        to_encode = struct.pack(">i", len(text)) + text + rnd_string[: padded_length - len(text) - 4]
        encryptor = cipher.encryptor()
        encoded = encryptor.update(to_encode) + encryptor.finalize()

        if base64:
            encoded = codecs.encode(encoded, "base64").strip()  # Return as bytes

        return encoded

    def derive_password(self, password: str | bytes, salt: bytes | str, length: int = 32) -> bytes:
        password = password.encode() if isinstance(password, str) else password
        salt = salt.encode() if isinstance(salt, str) else salt
        return hashlib.pbkdf2_hmac("sha256", password, salt, 100000, dklen=length)

    def aes256_cbc_decrypt(self, text: bytes, key: bytes, base64: bool = False) -> bytes:
        if base64:
            text = codecs.decode(text, "base64")

        cipher = Cipher(
            algorithms.AES(CryptoManager.ensure_aes_key(key, 16)),
            modes.CBC(b"udsinitvectoruds"),
            backend=default_backend(),
        )
        decryptor = cipher.decryptor()

        to_decode = decryptor.update(text) + decryptor.finalize()
        return to_decode[4 : 4 + struct.unpack(">i", to_decode[:4])[0]]

    def aes256_gcm_encrypt(self, key: bytes, nonce: bytes, plaintext: bytes, aad: bytes | None = None) -> bytes:
        if len(key) != 32:
            raise ValueError("AES-256-GCM key must be 32 bytes")
        if len(nonce) != 12:
            raise ValueError("AES-256-GCM nonce must be 12 bytes")

        aesgcm = aead.AESGCM(key)
        return aesgcm.encrypt(nonce, plaintext, aad)

    def aes256_gcm_decrypt(
        self, key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes | None = None
    ) -> bytes:
        if len(key) != 32:
            raise ValueError("AES-256-GCM key must be 32 bytes")
        if len(nonce) != 12:
            raise ValueError("AES-256-GCM nonce must be 12 bytes")

        aesgcm = aead.AESGCM(key)
        return aesgcm.decrypt(nonce, ciphertext, aad)

    # Fast encription using django SECRET_KEY as key
    def fast_crypt(self, data: bytes) -> bytes:
        return self.aes256_cbc_encrypt(data, UDSK)

    # Fast decryption using django SECRET_KEY as key
    def fast_decrypt(self, data: bytes) -> bytes:
        return self.aes256_cbc_decrypt(data, UDSK)

    def xor(self, value: str | bytes, key: str | bytes) -> bytes:
        if not key:
            return b""  # Protect against division by cero

        if isinstance(value, str):
            value = value.encode("utf-8")
        if isinstance(key, str):
            key = key.encode("utf-8")
        mult = len(value) // len(key) + 1
        value_array = array.array("B", value)
        # Ensure key array is at least as long as value_array
        key_array = array.array("B", key * mult)
        # We must return binary in xor, because result is in fact binary
        return array.array("B", (value_array[i] ^ key_array[i] for i in range(len(value_array)))).tobytes()

    def symmetric_encrypt(self, text: str | bytes, key: str | bytes) -> bytes:
        if isinstance(text, str):
            text = text.encode()
        if isinstance(key, str):
            key = key.encode()

        return self.aes256_cbc_encrypt(text, key)

    def symmetric_decrypt(self, encrypted_text: str | bytes, key: str | bytes) -> str:
        if isinstance(encrypted_text, str):
            encrypted_text = encrypted_text.encode()

        if isinstance(key, str):
            key = key.encode()

        if not encrypted_text or not key:
            return ""

        try:
            return self.aes256_cbc_decrypt(encrypted_text, key).decode("utf-8")
        except Exception:  # Error decoding crypted element, return empty one
            return ""

    def load_private_key(
        self, rsa_key: str
    ) -> "RSAPrivateKey | DSAPrivateKey | DHPrivateKey | EllipticCurvePrivateKey":
        try:
            return typing.cast(
                "RSAPrivateKey | DSAPrivateKey | DHPrivateKey | EllipticCurvePrivateKey",
                serialization.load_pem_private_key(rsa_key.encode(), password=None, backend=default_backend()),
            )
        except Exception as e:
            raise e

    def load_certificate(self, certificate: str | bytes) -> x509.Certificate:
        if isinstance(certificate, str):
            certificate = certificate.encode()

        # If invalid certificate, will raise an exception
        try:
            return x509.load_pem_x509_certificate(certificate, default_backend())
        except Exception as e:
            raise Exception("Invalid certificate") from e

    def certificate_string(self, certificate: str) -> str:
        # Remove -----.*-----\n strings using regex
        return re.sub(r"(-----.*-----\n)", "", certificate)

    def secret(self, length: int = 16) -> str:
        """
        Get a random secret string from config.SECRET_KEY
        """
        return settings.SECRET_KEY[:length]

    def salt(self, length: int = 16) -> str:
        """
        Get a random salt random string
        """
        return secrets.token_hex(length)

    def hash(self, value: str | bytes) -> str:
        if isinstance(value, str):
            value = value.encode()

        # Argon2
        return "{ARGON2}" + PasswordHasher(type=ArgonType.ID).hash(value)

    def check_hash(self, value: str | bytes, hash_value: str) -> bool:
        if isinstance(value, str):
            value = value.encode()

        if not value:
            return not hash_value

        if hash_value[:8] == "{SHA256}":
            return secrets.compare_digest(hashlib.sha3_256(value).hexdigest(), hash_value[8:])
        if hash_value[:12] == "{SHA256SALT}":
            # Extract 16 chars salt and hash
            salt = hash_value[12:28].encode()
            value = salt + value
            return secrets.compare_digest(hashlib.sha3_256(value).hexdigest(), hash_value[28:])
        # Argon2
        if hash_value[:8] == "{ARGON2}":
            ph = PasswordHasher()  # Type is implicit in hash
            try:
                ph.verify(hash_value[8:], value)
                return True
            except Exception:
                return False  # Verify will raise an exception if not valid

        # Old sha1
        return secrets.compare_digest(
            hash_value,
            hashlib.sha1(value).hexdigest(),  # nosec: Old SHA1 password, not used anymore but need to be supported
        )

    def uuid(self, obj: typing.Any = None) -> str:
        """Generates an uuid from obj. (lower case)
        If obj is None, returns a non-deterministic uuid (preferably uuid7 if available, else uuid4)
        """
        if obj is None:  # Non deterministic, try to use uuid7 if available
            if uuid7 is not None:
                return str(uuid7())
            return str(uuid.uuid4())
        elif isinstance(obj, bytes):
            obj = obj.decode("utf8")  # To string
        else:
            try:
                obj = str(obj)
            except Exception:
                obj = str(hash(obj))  # Get hash of object

        return str(uuid.uuid5(self._namespace, obj))  # Uuid is always lower case

    # Used to encode fields that will go inside json
    def encrypt_field_b64(self, plaintext: str, key_ascii32: str, nonce_seq: int) -> str:
        """
        Cipher a `plaintext` with AES-256-GCM using `key_ascii32` (32 bytes ASCII)
        and a nonce of 12 bytes with last one being a simple seq, starting at 1.

        Args:
            plaintext: The plaintext to encrypt.
            key_ascii32: The 32 bytes ASCII key to use for encryption.
            nonce_seq: The nonce sequence number (1, 2, 3...).

        Returns the ciphertext+tag in standard Base64.
        """
        key_bytes = key_ascii32.encode("ascii")
        if len(key_bytes) != 32:
            raise ValueError("The key must be exactly 32 bytes ASCII")

        # Nonce is 12 bytes with the last byte = nonce_seq
        nonce = bytearray(12)
        nonce[-1] = nonce_seq  # 1, 2, 3...

        # Initialize AES-GCM
        aesgcm = aead.AESGCM(key_bytes)
        return base64.b64encode(aesgcm.encrypt(bytes(nonce), plaintext.encode("utf-8"), None)).decode()

    def random_string(self, length: int = 40, digits: bool = True, punctuation: bool = False) -> str:
        base = (
            string.ascii_letters
            + (string.digits if digits else "")
            + (string.punctuation if punctuation else "")
        )
        return "".join(secrets.choice(base) for _ in range(length))

    def random_bytes(self, length: int = 32) -> bytes:
        return secrets.token_bytes(length)

    def unique(self) -> str:
        return hashlib.sha3_256(
            (self.random_string(24, True) + timezone.localtime().strftime("%H%M%S%f")).encode()
        ).hexdigest()

    def sha(self, value: str | bytes) -> str:
        if isinstance(value, str):
            value = value.encode()

        return hashlib.sha3_256(value).hexdigest()

    # RDP related
    def sign_rdp(self, data: str) -> str:
        """
        Signs the data using the key and returns the signature.
        """
        return rdp.sign_rdp(data)

    def derive_tunnel_material(self, shared_secret: bytes, ticket_id: bytes) -> types.crypto.TunnelMaterial:
        """
        Derives keys and nonces for payload + tunnel from a KEM/Kyber shared_secret.

        shared_secret: bytes from KEM/Kyber (same on client and broker/tunnel)
        ticket_id: 48-byte unique ID for this ticket/session (used as HKDF salt)
        """

        if len(ticket_id) < 48:
            raise ValueError(f"ticket_id must be at least 48 bytes, got {len(ticket_id)}")

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=108,
            salt=ticket_id,
            info=b"openuds-ticket-crypt",
        )
        okm = hkdf.derive(shared_secret)  # 108 bytes

        key_payload = okm[0:32]
        key_send = okm[32:64]
        key_receive = okm[64:96]
        nonce_payload = okm[96:108]  # 12 bytes

        return types.crypto.TunnelMaterial(
            key_payload=key_payload,
            key_send=key_send,
            key_receive=key_receive,
            nonce_payload=nonce_payload,
        )

    def generate_kem_shared_ciphertext(self, kem_key_b64: str) -> tuple[bytes, bytes]:
        """
        Given a base64-encoded KEM public key, generates a shared secret and ciphertext.

        Returns a tuple of (shared_secret: bytes, ciphertext: bytes)
        """
        return kem.encrypt(kem_key_b64)

    def encrypted_dict(
        self,
        dct: dict[str, typing.Any],
        ticket_id: str,
        *,
        kem_key_b64: str | None = None,
        shared_secret: bytes | None = None,
        ciphertext: bytes | None = None,
    ) -> tuple[bytes, dict[str, str]]:
        """
        Convert a dict to an encrypted dict using AES-256-GCM with keys derived from the shared_secret and ticket_id.

        Returns:
        A tuple of (shared_secret: bytes, encrypted_dict: dict[str, str])
        """
        if shared_secret is None or ciphertext is None:
            if kem_key_b64 is None:
                raise ValueError("Either kem_key_b64 or both shared_secret and ciphertext must be provided")
            shared_secret, ciphertext = self.generate_kem_shared_ciphertext(kem_key_b64)

        material = self.derive_tunnel_material(shared_secret, ticket_id.encode())

        plaintext = json.dumps(dct).encode()

        encrypted = self.aes256_gcm_encrypt(material.key_payload, material.nonce_payload, plaintext, b"")

        # used codecs instead of base64 to keep consistency with the use uf bz2 compression
        return (
            shared_secret,
            {
                "algorithm": "AES-256-GCM",
                "ciphertext": base64.b64encode(ciphertext).decode(),
                "data": base64.b64encode(encrypted).decode(),
            },
        )

    def decrypted_dict(
        self,
        encrypted_dict: dict[str, str],
        ticket_id: str,
        kem_private_key_b64: str,
    ) -> dict[str, typing.Any]:
        """
        Decrypts an encrypted dict using AES-256-GCM with keys derived from the shared_secret and ticket_id.

        Returns:
        The decrypted dict.
        """
        # Ensure algorithm is correct
        if encrypted_dict.get("algorithm") != "AES-256-GCM":
            raise ValueError("Unsupported encryption algorithm")

        ciphertext = base64.b64decode(encrypted_dict["ciphertext"])
        encrypted_data = base64.b64decode(encrypted_dict["data"])

        shared_secret = kem.decrypt(kem_private_key_b64, ciphertext)

        material = self.derive_tunnel_material(shared_secret, ticket_id.encode())

        decrypted = self.aes256_gcm_decrypt(material.key_payload, material.nonce_payload, encrypted_data, b"")

        return json.loads(decrypted.decode())

    def get_server_cert(self) -> str:
        return certs.get_server_cert()

    def get_server_key(self) -> str:
        return certs.get_server_key()

    def check_cert_chain(self, cert_chain: pathlib.Path | str | None = None) -> None:
        return certs.check_cert_chain(cert_chain or certs.get_server_cert())
