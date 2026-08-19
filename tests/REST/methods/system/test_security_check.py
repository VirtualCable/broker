#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
"""
Tests for the REST ``/system/security_check`` endpoint.

Notes
-----
* The endpoint requires administrator privileges for the security report; staff
  members receive ``403 Forbidden``.
* The test database starts with the shipped configuration defaults, so the
  expected summary is deterministic (see each test's comments).
"""

import typing

from uds.core import types, consts
from uds.core.util.config import GlobalConfig

from ....utils import rest

EXPECTED_CHECK_IDS: typing.Final[frozenset[str]] = frozenset(
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


class SecurityCheckEndpointTest(rest.test.RESTTestCase):
    PATH = "system/security_check"

    @typing.override
    def tearDown(self) -> None:
        # Restore the shipped default root password: config values keep an
        # in-memory copy that outlives the (truncated) test database.
        GlobalConfig.SUPER_USER_PASS.set(consts.security.DEFAULT_SUPERUSER_PASSWORD)
        super().tearDown()

    def _get_report(self) -> dict[str, typing.Any]:
        response = self.client.rest_get("system/security_check")
        self.assertEqual(response.status_code, 200, response.content)
        return typing.cast("dict[str, typing.Any]", response.json())

    def test_anonymous_access_is_denied(self) -> None:
        response = self.client.rest_get("system/security_check")
        self.assertEqual(response.status_code, 403, response.content)

    def test_staff_access_is_denied(self) -> None:
        self.login(as_admin=False)
        response = self.client.rest_get("system/security_check")
        self.assertEqual(response.status_code, 403, response.content)

    def test_admin_gets_full_report(self) -> None:
        self.login()
        body = self._get_report()
        checks = body["checks"]
        self.assertEqual({check["id"] for check in checks}, EXPECTED_CHECK_IDS)
        # Every check carries its severity, state and message
        for check in checks:
            self.assertIn(
                check["severity"], [severity.value for severity in types.security.SecurityCheckSeverity]
            )
            self.assertIsInstance(check["ok"], bool)
            self.assertTrue(check["message"])
        # Summary counts only failed checks, per severity
        for severity in types.security.SecurityCheckSeverity:
            expected = sum(1 for check in checks if not check["ok"] and check["severity"] == severity.value)
            self.assertEqual(body[severity.value], expected)

    def test_admin_report_reflects_configuration_state(self) -> None:
        self.login()
        # Isolate this test from the other CRITICAL/HIGH findings the test
        # settings trigger (DEBUG=True, PROFILING=True, ALLOWED_HOSTS=['*'],
        # CSRF middleware commented out). The superuser-credentials check is
        # the one we want to exercise here.
        middleware: list[str] = [
            "django.middleware.security.SecurityMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.middleware.common.CommonMiddleware",
        ]
        with self.settings(DEBUG=False, PROFILING=False, ALLOWED_HOSTS=["testserver"], MIDDLEWARE=middleware):
            body = self._get_report()
            critical = {
                check["id"] for check in body["checks"] if check["severity"] == "critical" and not check["ok"]
            }
            self.assertEqual(critical, {"default-superuser-credentials"})

            # Rotating the root password clears the only critical finding
            GlobalConfig.SUPER_USER_PASS.set("a-rotated-not-default-password")
            body = self._get_report()
            self.assertEqual(body["critical"], 0)
