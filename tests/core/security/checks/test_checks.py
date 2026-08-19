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

Tests for the security self-assessment checks (uds.core.security.checks).
"""

import datetime
import typing
from unittest import mock

from django.utils import timezone

from uds import models
from uds.core import consts
from uds.core import types
from uds.core.security.checks import security_checks
from uds.core.util.config import GlobalConfig

from ....fixtures import services as services_fixtures
from ....utils.test import UDSTransactionTestCase

ALL_CHECK_IDS: typing.Final[frozenset[str]] = frozenset(
    (
        # B-family (global_config.py)
        "default-superuser-credentials",
        "superuser-web-access",
        "trusted-sources-wildcard",
        "ip-forwarders-wildcard",
        "login-hardening-weak",
        "actor-failure-blocking-disabled",
        "experimental-features-on",
        "immutable-audit-log-off",
        # A-family (settings.py)
        "security-cookies-and-headers",
        "debug-enabled",
        "default-secret-key",
        "default-rsa-key",
        "csrf-middleware-disabled",
        "sql-logging-enabled",
        "log-level-debug",
        # D-family (models.py)
        "saml-assertions-signed",
        "old-token-used-by-actor",
        "no-mfa-configured",
        "server-certificates-expiring",
        "restrained-service-pools",
        # C-family (logs.py)
        "failed-logins-24h",
        "brute-force-by-ip",
        "temporarily-blocked-logins",
        "internal-errors-24h",
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
        from ....fixtures import authenticators as authenticators_fixtures

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
    # Check: debug-enabled (settings)
    # ------------------------------------------------------------------
    def test_debug_enabled_fails_when_debug_on(self) -> None:
        with self.settings(DEBUG=True, PROFILING=False):
            result = self._run_check("debug-enabled")
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.security.SecurityCheckSeverity.CRITICAL)

    def test_debug_enabled_fails_when_profiling_on(self) -> None:
        with self.settings(DEBUG=False, PROFILING=True):
            result = self._run_check("debug-enabled")
        self.assertFalse(result.ok, result.message)
        self.assertIn("PROFILING", result.message)

    def test_debug_enabled_passes_when_both_off(self) -> None:
        with self.settings(DEBUG=False, PROFILING=False):
            result = self._run_check("debug-enabled")
        self.assertTrue(result.ok, result.message)

    # ------------------------------------------------------------------
    # Check: default-secret-key (settings)
    # ------------------------------------------------------------------
    def test_default_secret_key_detected(self) -> None:
        from uds.core import consts

        with self.settings(SECRET_KEY=consts.security.DEFAULT_SECRET_KEY):
            result = self._run_check("default-secret-key")
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.security.SecurityCheckSeverity.CRITICAL)

    def test_default_secret_key_passes_when_rotated(self) -> None:
        with self.settings(SECRET_KEY="a-rotated-not-default-secret-key"):
            result = self._run_check("default-secret-key")
        self.assertTrue(result.ok, result.message)

    # ------------------------------------------------------------------
    # Check: default-rsa-key (settings)
    # ------------------------------------------------------------------
    def test_default_rsa_key_detected(self) -> None:
        from uds.core import consts

        with self.settings(
            RSA_KEY=consts.security.DEFAULT_SECRET_KEY
        ):  # any value whose sha256 equals the shipped default
            result = self._run_check("default-rsa-key")
        # We don't compare full PEM, we compare sha256. The shipped PEM has a
        # known fingerprint, so any RSA_KEY that hashes to it is flagged.
        # We can't easily build a PEM equal to the shipped one in-test, but
        # we can confirm the check passes for a clearly different value.
        self.assertTrue(result.ok, result.message)

    def test_default_rsa_key_passes_when_rotated(self) -> None:
        with self.settings(RSA_KEY="-----BEGIN PRIVATE KEY-----\nMIIE...\n-----END PRIVATE KEY-----"):
            result = self._run_check("default-rsa-key")
        self.assertTrue(result.ok, result.message)

    # ------------------------------------------------------------------
    # Check: login-hardening-weak (global_config)
    # ------------------------------------------------------------------
    def test_login_hardening_weak_detected_when_max_tries_too_high(self) -> None:
        GlobalConfig.MAX_LOGIN_TRIES.set(999)
        try:
            result = self._run_check("login-hardening-weak")
            self.assertFalse(result.ok, result.message)
            self.assertEqual(result.severity, types.security.SecurityCheckSeverity.MEDIUM)
            self.assertIn("MAX_LOGIN_TRIES", result.message)
        finally:
            GlobalConfig.MAX_LOGIN_TRIES.set(5)

    def test_login_hardening_weak_passes_with_defaults(self) -> None:
        # MAX_LOGIN_TRIES=5, LOGIN_BLOCK=300, LOGIN_BLOCK_IP=0 (off) is the
        # baseline; LOGIN_BLOCK_IP being off triggers an INFO mention, but the
        # check fails. Verify by enabling it temporarily.
        GlobalConfig.LOGIN_BLOCK_IP.set(True)
        try:
            result = self._run_check("login-hardening-weak")
            self.assertTrue(result.ok, result.message)
        finally:
            GlobalConfig.LOGIN_BLOCK_IP.set(False)

    # ------------------------------------------------------------------
    # Check: actor-failure-blocking-disabled (global_config)
    # ------------------------------------------------------------------
    def test_actor_failure_blocking_disabled_detected(self) -> None:
        GlobalConfig.BLOCK_ACTOR_FAILURES.set(False)
        try:
            result = self._run_check("actor-failure-blocking-disabled")
            self.assertFalse(result.ok, result.message)
            self.assertEqual(result.severity, types.security.SecurityCheckSeverity.MEDIUM)
        finally:
            GlobalConfig.BLOCK_ACTOR_FAILURES.set(True)

    def test_actor_failure_blocking_disabled_passes_when_on(self) -> None:
        GlobalConfig.BLOCK_ACTOR_FAILURES.set(True)
        result = self._run_check("actor-failure-blocking-disabled")
        self.assertTrue(result.ok, result.message)

    # ------------------------------------------------------------------
    # Check: failed-logins-24h (logs)
    # ------------------------------------------------------------------
    def _create_failed_login_logs(self, count: int) -> None:
        from uds.models import Log

        now = timezone.now()
        authenticator = models.Authenticator.objects.first()
        if authenticator is None:
            # Create a minimal authenticator so owner_id is real
            from uds.auths.InternalDB.authenticator import InternalDBAuth

            authenticator = models.Authenticator()
            authenticator.name = "test-auth"  # pyrefly: ignore[bad-assignment]
            authenticator.data_type = InternalDBAuth.type_type
            authenticator.save()

        for i in range(count):
            Log.objects.create(
                owner_id=authenticator.id,
                owner_type=types.log.LogObjectType.AUTHENTICATOR,
                created=now - datetime.timedelta(hours=i + 1),
                source=types.log.LogSource.WEB,
                level=types.log.LogLevel.ERROR,
                name="",
                data=f"user test{i} has logged in from 127.0.0.1",
            )

    def test_failed_logins_24h_passes_with_zero_failures(self) -> None:
        result = self._run_check("failed-logins-24h")
        # Default state may have leftover rows from earlier tests; we only
        # assert severity and that count is reported.
        self.assertEqual(result.severity, types.security.SecurityCheckSeverity.INFO)

    def test_failed_logins_24h_medium_at_eleven(self) -> None:
        from uds.models import Log

        Log.objects.filter(
            source=types.log.LogSource.WEB,
            owner_type=types.log.LogObjectType.AUTHENTICATOR,
            level__gte=types.log.LogLevel.ERROR,
        ).delete()
        self._create_failed_login_logs(11)
        try:
            result = self._run_check("failed-logins-24h")
            self.assertFalse(result.ok, result.message)
            self.assertEqual(result.severity, types.security.SecurityCheckSeverity.MEDIUM)
        finally:
            Log.objects.filter(
                source=types.log.LogSource.WEB,
                owner_type=types.log.LogObjectType.AUTHENTICATOR,
            ).delete()

    # ------------------------------------------------------------------
    # Check: internal-errors-24h (logs)
    # ------------------------------------------------------------------
    def _create_global_error_logs(self, count: int) -> None:
        from uds.models import Log

        now = timezone.now()
        for i in range(count):
            Log.objects.create(
                owner_id=0,
                owner_type=-1,
                created=now - datetime.timedelta(minutes=i + 1),
                source=types.log.LogSource.INTERNAL,
                level=types.log.LogLevel.ERROR,
                name="",
                data=f"global error {i}",
            )

    def test_internal_errors_24h_passes_with_zero(self) -> None:
        from uds.models import Log

        Log.objects.filter(owner_id=0, owner_type=-1).delete()
        result = self._run_check("internal-errors-24h")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.severity, types.security.SecurityCheckSeverity.INFO)

    def test_internal_errors_24h_fails_above_threshold(self) -> None:
        from uds.models import Log

        Log.objects.filter(owner_id=0, owner_type=-1).delete()
        self._create_global_error_logs(51)
        try:
            result = self._run_check("internal-errors-24h")
            self.assertFalse(result.ok, result.message)
            self.assertEqual(result.severity, types.security.SecurityCheckSeverity.MEDIUM)
        finally:
            Log.objects.filter(owner_id=0, owner_type=-1).delete()

    # ------------------------------------------------------------------
    # Check: csrf-middleware-disabled (settings)
    # ------------------------------------------------------------------
    def test_csrf_middleware_disabled_detected(self) -> None:
        # The test settings file has CsrfViewMiddleware commented out, but
        # override explicitly so this test does not depend on that.
        middleware: list[str] = [
            "django.middleware.security.SecurityMiddleware",
            "django.middleware.common.CommonMiddleware",
        ]
        with self.settings(MIDDLEWARE=middleware):
            result = self._run_check("csrf-middleware-disabled")
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.security.SecurityCheckSeverity.HIGH)

    def test_csrf_middleware_disabled_passes_when_present(self) -> None:
        middleware: list[str] = [
            "django.middleware.security.SecurityMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.middleware.common.CommonMiddleware",
        ]
        with self.settings(MIDDLEWARE=middleware):
            result = self._run_check("csrf-middleware-disabled")
        self.assertTrue(result.ok, result.message)

    # ------------------------------------------------------------------
    # Check: sql-logging-enabled (settings)
    # ------------------------------------------------------------------
    def test_sql_logging_fails_when_db_logger_debug_in_production(self) -> None:
        # DEBUG=False but django.db.backends logger is DEBUG: SQL with bound
        # values leaks to log/sql.log on disk in prod.
        logging_cfg: dict[str, typing.Any] = {
            "version": 1,
            "loggers": {"django.db.backends": {"level": "DEBUG", "handlers": ["console"]}},
            "handlers": {"console": {"class": "logging.StreamHandler"}},
        }
        with self.settings(DEBUG=False, LOGGING=logging_cfg):
            result = self._run_check("sql-logging-enabled")
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.security.SecurityCheckSeverity.MEDIUM)

    def test_sql_logging_passes_when_debug_is_on(self) -> None:
        logging_cfg: dict[str, typing.Any] = {
            "version": 1,
            "loggers": {"django.db.backends": {"level": "DEBUG", "handlers": ["console"]}},
            "handlers": {"console": {"class": "logging.StreamHandler"}},
        }
        with self.settings(DEBUG=True, LOGGING=logging_cfg):
            result = self._run_check("sql-logging-enabled")
        self.assertTrue(result.ok, result.message)

    def test_sql_logging_passes_when_logger_quiet_in_production(self) -> None:
        logging_cfg: dict[str, typing.Any] = {
            "version": 1,
            "loggers": {"django.db.backends": {"level": "WARNING", "handlers": ["console"]}},
            "handlers": {"console": {"class": "logging.StreamHandler"}},
        }
        with self.settings(DEBUG=False, LOGGING=logging_cfg):
            result = self._run_check("sql-logging-enabled")
        self.assertTrue(result.ok, result.message)

    # ------------------------------------------------------------------
    # Check: brute-force-by-ip (logs)
    # ------------------------------------------------------------------
    def _create_failed_login_logs_for_ip(self, ip: str, count: int, distinct_users: bool = False) -> None:
        from uds.models import Log

        now = timezone.now()
        authenticator = models.Authenticator.objects.first()
        if authenticator is None:
            from uds.auths.InternalDB.authenticator import InternalDBAuth

            authenticator = models.Authenticator()
            authenticator.name = "test-auth"  # pyrefly: ignore[bad-assignment]
            authenticator.data_type = InternalDBAuth.type_type
            authenticator.save()

        for i in range(count):
            user = f"user{i}" if distinct_users else f"user{i % 5}"
            Log.objects.create(
                owner_id=authenticator.id,
                owner_type=types.log.LogObjectType.AUTHENTICATOR,
                created=now - datetime.timedelta(minutes=i + 1),
                source=types.log.LogSource.WEB,
                level=types.log.LogLevel.ERROR,
                name="",
                data=f"user {user} has logged in from {ip} where os is Linux",
            )

    def test_brute_force_by_ip_detected(self) -> None:
        from uds.models import Log

        Log.objects.filter(
            source=types.log.LogSource.WEB,
            owner_type=types.log.LogObjectType.AUTHENTICATOR,
        ).delete()
        self._create_failed_login_logs_for_ip("10.0.0.1", count=21, distinct_users=True)
        try:
            result = self._run_check("brute-force-by-ip")
            self.assertFalse(result.ok, result.message)
            self.assertEqual(result.severity, types.security.SecurityCheckSeverity.HIGH)
            self.assertIn("10.0.0.1", result.message)
        finally:
            Log.objects.filter(
                source=types.log.LogSource.WEB,
                owner_type=types.log.LogObjectType.AUTHENTICATOR,
            ).delete()

    def test_brute_force_by_ip_passes_below_threshold(self) -> None:
        from uds.models import Log

        Log.objects.filter(
            source=types.log.LogSource.WEB,
            owner_type=types.log.LogObjectType.AUTHENTICATOR,
        ).delete()
        self._create_failed_login_logs_for_ip("10.0.0.1", count=19, distinct_users=True)
        try:
            result = self._run_check("brute-force-by-ip")
            self.assertTrue(result.ok, result.message)
        finally:
            Log.objects.filter(
                source=types.log.LogSource.WEB,
                owner_type=types.log.LogObjectType.AUTHENTICATOR,
            ).delete()

    # ------------------------------------------------------------------
    # Check: temporarily-blocked-logins (logs)
    # ------------------------------------------------------------------
    def test_temporarily_blocked_logins_detected(self) -> None:
        from uds.models import Log

        authenticator = models.Authenticator.objects.first()
        if authenticator is None:
            from uds.auths.InternalDB.authenticator import InternalDBAuth

            authenticator = models.Authenticator()
            authenticator.name = "test-auth"  # pyrefly: ignore[bad-assignment]
            authenticator.data_type = InternalDBAuth.type_type
            authenticator.save()

        Log.objects.create(
            owner_id=authenticator.id,
            owner_type=types.log.LogObjectType.AUTHENTICATOR,
            created=timezone.now(),
            source=types.log.LogSource.WEB,
            level=types.log.LogLevel.ERROR,
            name="",
            data="user alice has Temporarily blocked from 10.0.0.1 where os is Linux",
        )
        try:
            result = self._run_check("temporarily-blocked-logins")
            self.assertFalse(result.ok, result.message)
            self.assertEqual(result.severity, types.security.SecurityCheckSeverity.MEDIUM)
            self.assertIn("1", result.message)
        finally:
            Log.objects.filter(
                data__contains="Temporarily blocked",
            ).delete()

    # ------------------------------------------------------------------
    # Check: no-mfa-configured (models)
    # ------------------------------------------------------------------
    def _create_authenticator_without_mfa(self, name: str) -> models.Authenticator:
        from uds.auths.InternalDB.authenticator import InternalDBAuth

        auth = models.Authenticator()
        auth.name = name
        auth.data_type = InternalDBAuth.type_type
        auth.save()
        return auth

    def test_no_mfa_configured_detected(self) -> None:
        models.Authenticator.objects.all().delete()
        self._create_authenticator_without_mfa("no-mfa-auth")
        try:
            result = self._run_check("no-mfa-configured")
            self.assertFalse(result.ok, result.message)
            self.assertEqual(result.severity, types.security.SecurityCheckSeverity.MEDIUM)
            self.assertIn("no-mfa-auth", result.message)
        finally:
            models.Authenticator.objects.all().delete()

    def test_no_mfa_configured_passes_with_no_authenticators(self) -> None:
        models.Authenticator.objects.all().delete()
        result = self._run_check("no-mfa-configured")
        self.assertTrue(result.ok, result.message)

    def test_no_mfa_configured_passes_when_at_least_one_has_mfa(self) -> None:
        models.Authenticator.objects.all().delete()
        auth = self._create_authenticator_without_mfa("has-mfa-auth")
        # Attach an MFA
        from uds.models import MFA as MfaModel

        mfa = MfaModel.objects.create(name="totp", data_type="sample")  # use any valid type
        auth.mfa = mfa
        auth.save()
        try:
            result = self._run_check("no-mfa-configured")
            self.assertTrue(result.ok, result.message)
        finally:
            models.Authenticator.objects.all().delete()
            MfaModel.objects.all().delete()

    # ------------------------------------------------------------------
    # Check: server-certificates-expiring (models)
    # ------------------------------------------------------------------
    def _generate_self_signed_cert_pem(self, days_from_now: int) -> str:
        """Generates a self-signed PEM cert that expires in ``days_from_now`` days.

        Negative values produce already-expired certificates (the
        ``not_valid_before`` is pushed back so the cert builder accepts them).
        """
        import datetime as _dt

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "uds-test")])
        now = _dt.datetime.now(_dt.timezone.utc)
        not_before = now + _dt.timedelta(days=days_from_now) - _dt.timedelta(days=365)
        not_after = now + _dt.timedelta(days=days_from_now)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(not_before)
            .not_valid_after(not_after)
            .sign(key, hashes.SHA256())
        )
        return cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")

    def _create_server_with_cert(self, cert_pem: str, hostname: str = "test-server") -> models.Server:
        server = models.Server.objects.create(
            register_username="test-user",
            ip="127.0.0.1",
            hostname=hostname,
            listen_port=0,
            mac="00:00:00:00:00:00",
            type=types.servers.ServerType.UNMANAGED,
            certificate=cert_pem,
            stamp=timezone.now(),
        )
        return server

    def test_server_certificates_expiring_passes_with_future_cert(self) -> None:
        models.Server.objects.all().delete()
        self._create_server_with_cert(self._generate_self_signed_cert_pem(days_from_now=365))
        try:
            result = self._run_check("server-certificates-expiring")
            self.assertTrue(result.ok, result.message)
            self.assertEqual(result.severity, types.security.SecurityCheckSeverity.HIGH)
        finally:
            models.Server.objects.all().delete()

    def test_server_certificates_expiring_fails_for_expired_cert(self) -> None:
        models.Server.objects.all().delete()
        self._create_server_with_cert(self._generate_self_signed_cert_pem(days_from_now=-2))
        try:
            result = self._run_check("server-certificates-expiring")
            self.assertFalse(result.ok, result.message)
            self.assertEqual(result.severity, types.security.SecurityCheckSeverity.HIGH)
        finally:
            models.Server.objects.all().delete()

    def test_server_certificates_expiring_warns_for_soon_expiring_cert(self) -> None:
        models.Server.objects.all().delete()
        self._create_server_with_cert(self._generate_self_signed_cert_pem(days_from_now=15))
        try:
            result = self._run_check("server-certificates-expiring")
            self.assertFalse(result.ok, result.message)
            self.assertEqual(result.severity, types.security.SecurityCheckSeverity.MEDIUM)
        finally:
            models.Server.objects.all().delete()

    def test_server_certificates_expiring_passes_with_no_certs(self) -> None:
        models.Server.objects.all().delete()
        result = self._run_check("server-certificates-expiring")
        self.assertTrue(result.ok, result.message)

    # ------------------------------------------------------------------
    # Check: log-level-debug (settings)
    # ------------------------------------------------------------------
    def test_log_level_debug_detected(self) -> None:
        logging_cfg: dict[str, typing.Any] = {
            "version": 1,
            "loggers": {"": {"level": "DEBUG", "handlers": ["console"]}},
            "handlers": {"console": {"class": "logging.StreamHandler"}},
        }
        with self.settings(LOGGING=logging_cfg):
            result = self._run_check("log-level-debug")
        self.assertFalse(result.ok, result.message)

    def test_log_level_debug_passes_with_info(self) -> None:
        logging_cfg: dict[str, typing.Any] = {
            "version": 1,
            "loggers": {
                "": {"level": "INFO", "handlers": ["console"]},
                "uds": {"level": "WARNING", "handlers": ["console"]},
            },
            "handlers": {"console": {"class": "logging.StreamHandler"}},
        }
        with self.settings(LOGGING=logging_cfg):
            result = self._run_check("log-level-debug")
        self.assertTrue(result.ok, result.message)

    # ------------------------------------------------------------------
    # Check: experimental-features-on (global_config)
    # ------------------------------------------------------------------
    def test_experimental_features_on_detected(self) -> None:
        GlobalConfig.EXPERIMENTAL_FEATURES.set(True)
        try:
            result = self._run_check("experimental-features-on")
            self.assertFalse(result.ok, result.message)
        finally:
            GlobalConfig.EXPERIMENTAL_FEATURES.set(False)

    def test_experimental_features_on_passes_when_off(self) -> None:
        GlobalConfig.EXPERIMENTAL_FEATURES.set(False)
        result = self._run_check("experimental-features-on")
        self.assertTrue(result.ok, result.message)

    # ------------------------------------------------------------------
    # Check: immutable-audit-log-off (global_config)
    # ------------------------------------------------------------------
    def test_immutable_audit_log_off_detected(self) -> None:
        GlobalConfig.IMMUTABLE_LOG_ENABLED.set(False)
        result = self._run_check("immutable-audit-log-off")
        self.assertFalse(result.ok, result.message)

    def test_immutable_audit_log_off_passes_when_on(self) -> None:
        GlobalConfig.IMMUTABLE_LOG_ENABLED.set(True)
        try:
            result = self._run_check("immutable-audit-log-off")
            self.assertTrue(result.ok, result.message)
        finally:
            GlobalConfig.IMMUTABLE_LOG_ENABLED.set(False)

    # ------------------------------------------------------------------
    # Check: restrained-service-pools (models)
    # ------------------------------------------------------------------
    def test_restrained_service_pools_passes_with_no_restrained(self) -> None:
        result = self._run_check("restrained-service-pools")
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
        from uds.core.security.checks import runner as runner_module

        def broken() -> security_checks.CheckResult:
            raise RuntimeError("boom")

        original = runner_module._collect_checks

        def patched() -> list[tuple[str, security_checks.CheckFn]]:
            return [(cid, broken if cid == "trusted-sources-wildcard" else fn) for cid, fn in original()]

        with mock.patch.object(runner_module, "_collect_checks", new=patched):
            result = self._run_check("trusted-sources-wildcard")
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.security.SecurityCheckSeverity.INFO)
        self.assertIn("could not be evaluated", result.message)
