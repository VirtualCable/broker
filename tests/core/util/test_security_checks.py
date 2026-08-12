# -*- coding: utf-8 -*-

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
Author: Adolfo Gómez, dkmaster at dkmon dot com

Tests for the security self-assessment checks (uds.core.util.security_checks).
"""

import typing
from unittest import mock

from uds import models
from uds.core import consts
from uds.core import types
from uds.core.util import security_checks
from uds.core.util.config import GlobalConfig

from ...fixtures import services as services_fixtures
from ...utils.test import UDSTransactionTestCase

ALL_CHECK_IDS: typing.Final[frozenset[str]] = frozenset(
    (
        "default-superuser-credentials",
        "superuser-web-access",
        "trusted-sources-wildcard",
        "ip-forwarders-wildcard",
        "security-cookies-and-headers",
        "saml-assertions-signed",
        "old-token-used-by-actor",
    )
)


class SecurityChecksTest(UDSTransactionTestCase):
    @typing.override
    def setUp(self) -> None:
        super().setUp()
        # Normalize the security configuration to a known baseline
        GlobalConfig.SUPER_USER_PASS.set(security_checks.DEFAULT_SUPERUSER_PASSWORD)
        GlobalConfig.SUPER_USER_ALLOW_WEBACCESS.set(True)
        GlobalConfig.TRUSTED_SOURCES.set("*")
        GlobalConfig.ADMIN_TRUSTED_SOURCES.set("*")
        GlobalConfig.BEHIND_PROXY.set(False)
        GlobalConfig.ALLOWED_IP_FORWARDERS.set("*")
        GlobalConfig.ENHANCED_SECURITY.set(True)

    @typing.override
    def tearDown(self) -> None:
        # Restore the shipped defaults: config values keep an in-memory copy
        # that outlives the (truncated) test database and would leak into
        # other tests running on the same process.
        GlobalConfig.SUPER_USER_PASS.set(security_checks.DEFAULT_SUPERUSER_PASSWORD)
        GlobalConfig.SUPER_USER_ALLOW_WEBACCESS.set(True)
        GlobalConfig.TRUSTED_SOURCES.set("*")
        GlobalConfig.ADMIN_TRUSTED_SOURCES.set("*")
        GlobalConfig.BEHIND_PROXY.set(False)
        GlobalConfig.ALLOWED_IP_FORWARDERS.set("*")
        GlobalConfig.ENHANCED_SECURITY.set(True)
        super().tearDown()

    def _run_check(self, check_id: str) -> types.security.SecurityCheckResult:
        results = {result.id: result for result in security_checks.run_security_checks()}
        self.assertIn(check_id, results)
        return results[check_id]

    def _create_saml_authenticator(
        self,
        name: str,
        *,
        assertions_signed: bool = False,
        messages_signed: bool = False,
    ) -> models.Authenticator:
        from uds.auths.SAML import SAMLAuthenticator

        authenticator = models.Authenticator()
        authenticator.name = name
        authenticator.data_type = SAMLAuthenticator.type_type
        authenticator.save()
        instance = typing.cast("SAMLAuthenticator", authenticator.get_instance())
        instance.want_assertions_signed.value = assertions_signed
        instance.want_messages_signed.value = messages_signed
        authenticator.data = instance.serialize()
        authenticator.save()

        return authenticator

    # ------------------------------------------------------------------
    # Check 1: default superuser credentials
    # ------------------------------------------------------------------
    def test_default_superuser_credentials_detected(self) -> None:
        result = self._run_check("default-superuser-credentials")
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.security.SecurityCheckSeverity.CRITICAL)
        self.assertIn("root password", result.message.lower())

    def test_rotated_superuser_credentials_pass(self) -> None:
        GlobalConfig.SUPER_USER_PASS.set("a-rotated-not-default-password")
        result = self._run_check("default-superuser-credentials")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.severity, types.security.SecurityCheckSeverity.CRITICAL)

    # ------------------------------------------------------------------
    # Check 2: root account web/API access
    # ------------------------------------------------------------------
    def test_superuser_web_access_enabled_fails(self) -> None:
        GlobalConfig.SUPER_USER_ALLOW_WEBACCESS.set(True)
        result = self._run_check("superuser-web-access")
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.security.SecurityCheckSeverity.MEDIUM)

    def test_superuser_web_access_disabled_passes(self) -> None:
        GlobalConfig.SUPER_USER_ALLOW_WEBACCESS.set(False)
        result = self._run_check("superuser-web-access")
        self.assertTrue(result.ok, result.message)

    # ------------------------------------------------------------------
    # Check 3: trusted sources wildcards
    # ------------------------------------------------------------------
    def test_wildcard_trusted_sources_detected(self) -> None:
        # Baseline: both TRUSTED_SOURCES and ADMIN_TRUSTED_SOURCES are "*"
        result = self._run_check("trusted-sources-wildcard")
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.security.SecurityCheckSeverity.MEDIUM)
        self.assertIn("TRUSTED_SOURCES", result.message)
        self.assertIn("ADMIN_TRUSTED_SOURCES", result.message)

        # Only the admin sources remain as wildcard
        GlobalConfig.TRUSTED_SOURCES.set("127.0.0.1")
        result = self._run_check("trusted-sources-wildcard")
        self.assertFalse(result.ok, result.message)
        self.assertTrue(result.message.startswith("ADMIN_TRUSTED_SOURCES"), result.message)

    def test_restricted_trusted_sources_pass(self) -> None:
        GlobalConfig.TRUSTED_SOURCES.set("127.0.0.1")
        GlobalConfig.ADMIN_TRUSTED_SOURCES.set("127.0.0.1")
        result = self._run_check("trusted-sources-wildcard")
        self.assertTrue(result.ok, result.message)

    # ------------------------------------------------------------------
    # Check 4: proxy / X-Forwarded-For trust
    # ------------------------------------------------------------------
    def test_ip_forwarders_info_when_not_behind_proxy(self) -> None:
        GlobalConfig.BEHIND_PROXY.set(False)
        result = self._run_check("ip-forwarders-wildcard")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.severity, types.security.SecurityCheckSeverity.INFO)

    def test_ip_forwarders_wildcard_behind_proxy_fails(self) -> None:
        GlobalConfig.BEHIND_PROXY.set(True)
        GlobalConfig.ALLOWED_IP_FORWARDERS.set("*")
        result = self._run_check("ip-forwarders-wildcard")
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.security.SecurityCheckSeverity.HIGH)

    def test_ip_forwarders_restricted_behind_proxy_passes(self) -> None:
        GlobalConfig.BEHIND_PROXY.set(True)
        GlobalConfig.ALLOWED_IP_FORWARDERS.set("10.0.0.1")
        result = self._run_check("ip-forwarders-wildcard")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.severity, types.security.SecurityCheckSeverity.HIGH)

    # ------------------------------------------------------------------
    # Check 8: session / security headers
    # ------------------------------------------------------------------
    def test_disabled_security_cookies_detected(self) -> None:
        # The test settings do not enable the "Secure" cookie flags
        result = self._run_check("security-cookies-and-headers")
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.security.SecurityCheckSeverity.LOW)
        self.assertIn("SESSION_COOKIE_SECURE", result.message)
        self.assertIn("CSRF_COOKIE_SECURE", result.message)

    def test_enabled_security_cookies_pass(self) -> None:
        with self.settings(
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SECURE=True,
            CSRF_COOKIE_HTTPONLY=True,
            CSRF_COOKIE_SECURE=True,
        ):
            result = self._run_check("security-cookies-and-headers")
        self.assertTrue(result.ok, result.message)

    # ------------------------------------------------------------------
    # Check 9: SAML assertion signing
    # ------------------------------------------------------------------
    def test_saml_check_passes_without_saml_authenticators(self) -> None:
        result = self._run_check("saml-assertions-signed")
        self.assertTrue(result.ok, result.message)
        self.assertIn("No SAML authenticators", result.message)

    def test_saml_unsigned_assertions_detected(self) -> None:
        self._create_saml_authenticator("Unsigned SAML")
        result = self._run_check("saml-assertions-signed")
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.security.SecurityCheckSeverity.MEDIUM)
        self.assertIn("Unsigned SAML", result.message)

    def test_saml_signed_assertions_pass(self) -> None:
        self._create_saml_authenticator("Signed SAML", assertions_signed=True)
        result = self._run_check("saml-assertions-signed")
        self.assertTrue(result.ok, result.message)

    def test_saml_signed_messages_pass(self) -> None:
        # Requiring signed messages is also accepted as signature enforcement
        self._create_saml_authenticator("Messages SAML", messages_signed=True)
        result = self._run_check("saml-assertions-signed")
        self.assertTrue(result.ok, result.message)

    # ------------------------------------------------------------------
    # Check 10: old (legacy uuid) actor token flow
    # ------------------------------------------------------------------
    def _create_userservice_with_actor_version(self, version: str) -> models.UserService:
        from ...fixtures import authenticators as authenticators_fixtures

        auth = authenticators_fixtures.create_db_authenticator()
        groups = authenticators_fixtures.create_db_groups(auth, 1)
        user = authenticators_fixtures.create_db_users(auth, 1, groups=groups)[0]

        userservice = services_fixtures.create_db_one_assigned_userservice(
            services_fixtures.create_db_provider(),
            user,
            groups,
            "managed",
        )
        userservice.actor_version = version
        userservice.save()
        return userservice

    def test_old_token_with_actor_version_detected(self) -> None:
        # A freshly created user service has the never-rotated (AUTO) token prefix
        userservice = self._create_userservice_with_actor_version("5.0.0")
        self.assertTrue(userservice.token.startswith(consts.auth.AUTO_TOKEN_PREFIX_NOT_USED))

        result = self._run_check("old-token-used-by-actor")
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.security.SecurityCheckSeverity.MEDIUM)
        self.assertIn(userservice.service_pool.name, result.message)

    def test_old_token_without_actor_version_passes(self) -> None:
        # Never-rotated token but no actor has reported a version yet
        self._create_userservice_with_actor_version("0.0.0")
        result = self._run_check("old-token-used-by-actor")
        self.assertTrue(result.ok, result.message)

    def test_rotated_token_with_actor_version_passes(self) -> None:
        # Actor uses the new token flow (token rotated, ust- prefix)
        from uds.models.user_service import create_actor_token

        userservice = self._create_userservice_with_actor_version("5.0.0")
        userservice.token = create_actor_token()
        userservice.save()
        self.assertFalse(userservice.token.startswith(consts.auth.AUTO_TOKEN_PREFIX_NOT_USED))

        result = self._run_check("old-token-used-by-actor")
        self.assertTrue(result.ok, result.message)

    # ------------------------------------------------------------------
    # Runner and report
    # ------------------------------------------------------------------
    def test_run_returns_all_checks(self) -> None:
        results = security_checks.run_security_checks()
        self.assertEqual({result.id for result in results}, ALL_CHECK_IDS)

    def test_build_report_summary_counts_failed_checks_only(self) -> None:
        results = security_checks.run_security_checks()
        report = security_checks.build_report(results)
        self.assertEqual(report["checks"], [result.as_dict() for result in results])
        for severity in types.security.SecurityCheckSeverity:
            expected = sum(1 for result in results if not result.ok and result.severity is severity)
            self.assertEqual(report[severity.value], expected)

    def test_failing_check_does_not_abort_scan(self) -> None:
        def broken() -> security_checks._CheckResult:
            raise RuntimeError("boom")

        checks = tuple(
            (check_id, broken) if check_id == "trusted-sources-wildcard" else (check_id, check)
            for check_id, check in security_checks._CHECKS
        )
        with mock.patch.object(security_checks, "_CHECKS", new=checks):
            result = self._run_check("trusted-sources-wildcard")
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.security.SecurityCheckSeverity.INFO)
        self.assertIn("could not be evaluated", result.message)
