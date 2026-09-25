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
Author: Andres Schumann, aschumann at virtualcable dot es

Tests for the authenticators health manual check (uds.checks.authenticators)
and the SAML IdP certificate health check it aggregates.
"""

import base64
import datetime
import typing
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from uds import models
from uds.core import types
from uds.core.checks import runner as runner_module

from ..fixtures import authenticators as authenticators_fixtures
from ..utils.test import UDSTransactionTestCase

if typing.TYPE_CHECKING:
    from uds.auths.SAML import SAMLAuthenticator


def _certificate_valid_for(days: int) -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "idp.example.com")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=400))
        .not_valid_after(now + datetime.timedelta(days=days))
        .sign(key, hashes.SHA256())
    )
    return base64.b64encode(certificate.public_bytes(serialization.Encoding.DER)).decode()


def _metadata(certificate: str) -> str:
    return (
        '<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"'
        ' xmlns:ds="http://www.w3.org/2000/09/xmldsig#" entityID="https://idp.example.com">'
        '<md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
        '<md:KeyDescriptor use="signing"><ds:KeyInfo><ds:X509Data>'
        f"<ds:X509Certificate>{certificate}</ds:X509Certificate>"
        "</ds:X509Data></ds:KeyInfo></md:KeyDescriptor>"
        '<md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"'
        ' Location="https://idp.example.com/sso"/>'
        "</md:IDPSSODescriptor></md:EntityDescriptor>"
    )


class AuthenticatorsHealthCheckTest(UDSTransactionTestCase):
    def _run_check(self) -> types.checks.CheckResult:
        results = {result.id: result for result in runner_module.run_checks(types.checks.CheckKind.MANUAL)}
        return results["authenticators-health"]

    def _create_saml(self, name: str, metadata: str) -> None:
        from uds.auths.SAML import SAMLAuthenticator

        authenticator = models.Authenticator(name=name, data_type=SAMLAuthenticator.type_type)
        authenticator.save()
        instance = typing.cast("SAMLAuthenticator", authenticator.get_instance())
        instance.idp_metadata.value = metadata
        authenticator.data = instance.serialize()
        authenticator.save()

    def test_passes_with_authenticators_without_health_check(self) -> None:
        authenticators_fixtures.create_db_authenticator()
        result = self._run_check()
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.category, types.checks.CheckCategory.HEALTH)

    def test_passes_with_a_long_lived_idp_certificate(self) -> None:
        self._create_saml("Corporate IdP", _metadata(_certificate_valid_for(365)))
        self.assertTrue(self._run_check().ok)

    def test_fails_when_the_idp_certificate_is_about_to_expire(self) -> None:
        self._create_saml("Corporate IdP", _metadata(_certificate_valid_for(10)))
        result = self._run_check()
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.checks.CheckSeverity.HIGH)
        self.assertIn("Corporate IdP", result.message)
        self.assertIn("expires on", result.message)
        self.assertEqual(len(result.details), 1)
        self.assertTrue(result.details[0].startswith("Corporate IdP: "), result.details)

    def test_fails_when_the_idp_certificate_has_expired(self) -> None:
        self._create_saml("Corporate IdP", _metadata(_certificate_valid_for(-1)))
        result = self._run_check()
        self.assertFalse(result.ok, result.message)
        self.assertIn("expired on", result.message)

    def test_reports_unreadable_metadata(self) -> None:
        from uds.auths.SAML import SAMLAuthenticator

        self._create_saml("Unreachable IdP", _metadata(_certificate_valid_for(365)))
        with mock.patch.object(SAMLAuthenticator, "get_idp_metadata_dict", side_effect=Exception("timeout")):
            result = self._run_check()
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.checks.CheckSeverity.MEDIUM)
        self.assertIn("Unreachable IdP", result.message)
        self.assertIn("timeout", result.message)

    def test_automatic_scan_does_not_run_it(self) -> None:
        ids = {result.id for result in runner_module.run_checks(types.checks.CheckKind.AUTOMATIC)}
        self.assertNotIn("authenticators-health", ids)
