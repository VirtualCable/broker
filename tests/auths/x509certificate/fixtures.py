#
# Copyright (c) 2024 Virtual Cable S.L.
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

import contextlib
import dataclasses
import datetime
import hashlib
import hmac as hmac_module
import json
import os
import typing

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives import hashes, padding as sym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

from uds.core.environment import Environment
from uds.auths.X509Certificate.authenticator import X509CertificateAuthenticator
from uds.core.types.auth import AuthTypeGroup

# Test shared secret — must match DATA_TEMPLATE
_TEST_SHARED_SECRET = 'test-shared-secret'


def _derive_keys(shared_secret: str) -> tuple[bytes, bytes]:
    secret = shared_secret.encode()
    enc_key = hashlib.sha256(secret + b'enc').digest()
    mac_key = hashlib.sha256(secret + b'mac').digest()
    return enc_key, mac_key


def _encrypt_payload(cert_pem: str, shared_secret: str = _TEST_SHARED_SECRET) -> str:
    """Simulate the bridge service: encrypt + sign a cert payload. Returns base64 string."""
    import base64

    payload = json.dumps({'cert': cert_pem}).encode()
    enc_key, mac_key = _derive_keys(shared_secret)

    # AES-256-CBC
    iv = os.urandom(16)
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(payload) + padder.finalize()

    cipher = Cipher(algorithms.AES(enc_key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()

    # HMAC
    mac = hmac_module.new(mac_key, iv + ciphertext, hashlib.sha256).digest()

    return base64.b64encode(iv + ciphertext + mac).decode()


def _build_name(**kwargs: str) -> x509.Name:
    """Build an x509.Name from keyword arguments (CN=..., O=..., etc.)."""
    oid_map: dict[str, x509.ObjectIdentifier] = {
        'CN': NameOID.COMMON_NAME,
        'O': NameOID.ORGANIZATION_NAME,
        'OU': NameOID.ORGANIZATIONAL_UNIT_NAME,
        'C': NameOID.COUNTRY_NAME,
        'ST': NameOID.STATE_OR_PROVINCE_NAME,
        'L': NameOID.LOCALITY_NAME,
        'SERIALNUMBER': NameOID.SERIAL_NUMBER,
        'E': NameOID.EMAIL_ADDRESS,
    }
    return x509.Name([x509.NameAttribute(oid_map[k], v) for k, v in kwargs.items()])


def _build_cert(
    subject: x509.Name,
    issuer: x509.Name,
    subject_key: rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey,
    issuer_key: rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey,
    serial: int,
    ca: bool = False,
) -> x509.Certificate:
    """Build a certificate signed by issuer_key."""
    from cryptography.x509 import BasicConstraints, CertificateBuilder

    builder = (
        CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(subject_key.public_key())
        .serial_number(serial)
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365))
        .add_extension(BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    return builder.sign(issuer_key, hashes.SHA256())


@dataclasses.dataclass
class CertFixture:
    """Container for a generated CA + client certificate pair."""

    ca_key: rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey
    ca_cert: x509.Certificate
    ca_pem: str
    client_key: rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey
    client_cert: x509.Certificate
    client_pem: str
    client_subject_dn: str
    client_issuer_dn: str
    username: str


def make_rsa_fixture(
    ca_cn: str = 'Test RSA CA',
    client_cn: str = 'testuser_rsa',
) -> CertFixture:
    """Generate an RSA CA + client certificate pair for testing."""
    ca_key = rsa.generate_private_key(65537, 2048)
    ca_name = _build_name(CN=ca_cn)
    ca_cert = _build_cert(ca_name, ca_name, ca_key, ca_key, serial=1, ca=True)
    ca_pem = ca_cert.public_bytes(Encoding.PEM).decode()

    client_key = rsa.generate_private_key(65537, 2048)
    client_name = _build_name(CN=client_cn)
    client_cert = _build_cert(client_name, ca_name, client_key, ca_key, serial=2)
    client_pem = client_cert.public_bytes(Encoding.PEM).decode()

    return CertFixture(
        ca_key=ca_key,
        ca_cert=ca_cert,
        ca_pem=ca_pem,
        client_key=client_key,
        client_cert=client_cert,
        client_pem=client_pem,
        client_subject_dn=client_cert.subject.rfc4514_string(),
        client_issuer_dn=client_cert.issuer.rfc4514_string(),
        username=client_cn,
    )


def make_ec_fixture(
    ca_cn: str = 'Test EC CA',
    client_cn: str = 'testuser_ec',
) -> CertFixture:
    """Generate an EC CA + client certificate pair for testing."""
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = _build_name(CN=ca_cn)
    ca_cert = _build_cert(ca_name, ca_name, ca_key, ca_key, serial=1, ca=True)
    ca_pem = ca_cert.public_bytes(Encoding.PEM).decode()

    client_key = ec.generate_private_key(ec.SECP256R1())
    client_name = _build_name(CN=client_cn)
    client_cert = _build_cert(client_name, ca_name, client_key, ca_key, serial=2)
    client_pem = client_cert.public_bytes(Encoding.PEM).decode()

    return CertFixture(
        ca_key=ca_key,
        ca_cert=ca_cert,
        ca_pem=ca_pem,
        client_key=client_key,
        client_cert=client_cert,
        client_pem=client_pem,
        client_subject_dn=client_cert.subject.rfc4514_string(),
        client_issuer_dn=client_cert.issuer.rfc4514_string(),
        username=client_cn,
    )


DATA_TEMPLATE: dict[str, str] = {
    'name': 'X509Certificate',
    'ca_certificate': '',  # filled by fixture
    'trusted_issuer': '',  # optional
    'username_attr': 'CN=([^,]*)',
    'realname_attr': 'CN=([^,]*)',
    'common_groups': 'x509_users',
    'remote_url': 'https://cert-auth.example.com',
    'shared_secret': _TEST_SHARED_SECRET,
}


@contextlib.contextmanager
def create_authenticator(
    fixture: CertFixture,
    trusted_issuer: str = '',
    username_attr: str = 'CN=([^,]*)',
    common_groups: str = 'x509_users',
) -> typing.Iterator[X509CertificateAuthenticator]:
    with Environment.temporary_environment() as env:
        data = DATA_TEMPLATE.copy()
        data['ca_certificate'] = fixture.ca_pem
        data['trusted_issuer'] = trusted_issuer
        data['username_attr'] = username_attr
        data['common_groups'] = common_groups
        instance = X509CertificateAuthenticator(environment=env, values=data)
        yield instance
