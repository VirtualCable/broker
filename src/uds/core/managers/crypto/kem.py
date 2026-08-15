# Copyright (c) 2019-2026 Virtual Cable S.L.
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
# Unified KEM interface for OpenUDS

import base64
import importlib
import typing

# Note, clients must use the same KEM module (kyber512, kyber768, kyber1024)


class _KemModule(typing.Protocol):
    """
    Protocol for a pqcrypto KEM module (pqcrypto >= 1.0.0).
    """

    PUBLIC_KEY_SIZE: int
    SECRET_KEY_SIZE: int

    def keygen(self) -> tuple[bytes, bytes]: ...

    def encaps(self, public_key: bytes) -> tuple[bytes, bytes]: ...

    def decaps(self, secret_key: bytes, ciphertext: bytes) -> bytes: ...


# pqcrypto >= 1.0.0 exposes the KEM submodules dynamically (loaded from the
# native extension), so a plain ``from pqcrypto.kem import ml_kem_768`` is not
# statically resolvable by type checkers. Import it through importlib and cast
# it to the protocol above.
kyber: _KemModule = typing.cast(
    _KemModule,
    importlib.import_module("pqcrypto.kem.ml_kem_768"),
)


def encrypt(kem_key_b64: str) -> tuple[bytes, bytes]:
    """
    Given a base64-encoded KEM public key, generates a shared key and ciphertext.

    Returns a tuple of (shared_key: bytes, ciphertext: bytes)
    """
    kem_key = base64.b64decode(kem_key_b64)

    # Note: this may be already tested in pqcrypto, but we ensure that is correct here
    # just in case a future version does not check it or we switch to another library
    if len(kem_key) != kyber.PUBLIC_KEY_SIZE:
        raise ValueError(f"KEM key must be {kyber.PUBLIC_KEY_SIZE} bytes")

    ciphertext, shared_key = kyber.encaps(kem_key)

    return shared_key, ciphertext


def decrypt(kem_private_key_b64: str, ciphertext: bytes) -> bytes:
    """
    Given a base64-encoded KEM private key (kem secret) and a ciphertext, returns the shared key.

    Returns shared_key: bytes
    """
    kem_private_key = base64.b64decode(kem_private_key_b64)

    # Note: this may be already tested in pqcrypto, but we ensure that is correct here
    # just in case a future version does not check it or we switch to another library
    if len(kem_private_key) != kyber.SECRET_KEY_SIZE:
        raise ValueError(f"KEM private key must be {kyber.SECRET_KEY_SIZE} bytes")

    shared_key = kyber.decaps(kem_private_key, ciphertext)

    return shared_key


def generate_keypair() -> tuple[str, str]:
    """
    Generates a new KEM keypair.

    Returns a tuple of (public_key_b64: str, private_key_b64: str)
    """
    public_key, private_key = kyber.keygen()

    public_key_b64 = base64.b64encode(public_key).decode("utf-8")
    private_key_b64 = base64.b64encode(private_key).decode("utf-8")

    return public_key_b64, private_key_b64
