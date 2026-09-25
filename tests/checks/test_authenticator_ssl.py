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

Tests for the authenticator TLS verification check (uds.checks.models).
"""

import collections.abc
import typing

from uds import models
from uds.core import types
from uds.core.checks import runner as runner_module

from ..utils.test import UDSTransactionTestCase

if typing.TYPE_CHECKING:
    from uds.auths.RegexLdap import RegexLdap
    from uds.auths.SAML import SAMLAuthenticator
    from uds.auths.SimpleLDAP import SimpleLDAPAuthenticator
    from uds.core import auths

AuthT = typing.TypeVar("AuthT", bound="auths.Authenticator")


class AuthenticatorSslVerificationCheckTest(UDSTransactionTestCase):
    def _run_check(self) -> types.checks.CheckResult:
        results = {result.id: result for result in runner_module.run_checks(types.checks.CheckKind.AUTOMATIC)}
        return results["authenticator-ssl-verification-disabled"]

    def _create(
        self, name: str, auth_type: type[AuthT], configure: collections.abc.Callable[[AuthT], None]
    ) -> None:
        authenticator = models.Authenticator(name=name, data_type=auth_type.type_type)
        authenticator.save()
        instance = typing.cast(AuthT, authenticator.get_instance())
        configure(instance)
        authenticator.data = instance.serialize()
        authenticator.save()

    def test_passes_without_authenticators(self) -> None:
        result = self._run_check()
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.category, types.checks.CheckCategory.SECURITY)

    def test_fails_on_ldaps_without_verification(self) -> None:
        from uds.auths.SimpleLDAP import SimpleLDAPAuthenticator

        def configure(ldap: "SimpleLDAPAuthenticator") -> None:
            ldap.use_ssl.value = True
            ldap.verify_ssl.value = False

        self._create("Plain LDAPS", SimpleLDAPAuthenticator, configure)
        result = self._run_check()
        self.assertFalse(result.ok, result.message)
        self.assertIn("Plain LDAPS", result.message)

    def test_passes_on_plain_ldap_or_verified_ldaps(self) -> None:
        from uds.auths.RegexLdap import RegexLdap

        def no_tls(ldap: "RegexLdap") -> None:
            ldap.use_ssl.value = False
            ldap.verify_ssl.value = False

        def verified(ldap: "RegexLdap") -> None:
            ldap.use_ssl.value = True
            ldap.verify_ssl.value = True

        self._create("No TLS", RegexLdap, no_tls)
        self._create("Verified", RegexLdap, verified)
        self.assertTrue(self._run_check().ok)

    def test_fails_on_saml_metadata_url_without_verification(self) -> None:
        from uds.auths.SAML import SAMLAuthenticator

        def configure(saml: "SAMLAuthenticator") -> None:
            saml.idp_metadata.value = "https://idp.example.com/metadata"
            saml.check_https_certificate.value = False

        self._create("SAML URL", SAMLAuthenticator, configure)
        result = self._run_check()
        self.assertFalse(result.ok, result.message)
        self.assertIn("SAML URL", result.message)
