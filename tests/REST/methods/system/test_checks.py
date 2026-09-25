#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
"""
Tests for the REST ``/system/checks`` endpoint.

Notes
-----
* The endpoint requires administrator privileges; staff members receive
  ``403 Forbidden``.
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
        "clock-skew-24h",
        # E-family (webhook_queue.py)
        "webhook-queue-size",
        # HEALTH family (deferred_deletion.py, publications.py)
        "deferred-deletion-stuck",
        "stuck-publications",
        # HEALTH family (cluster.py)
        "cluster-nodes-timezones",
        "cluster-node-clock-drift",
        "db-clock-spread",
        # Checks from support tickets (models.py, user_services.py, providers.py,
        # database.py, network.py, osmanagers.py)
        "authenticator-ssl-verification-disabled",
        "user-services-stuck-preparing",
        "stale-user-services",
        "provider-errors-24h",
        "provider-in-maintenance-mode",
        "db-app-time-mismatch",
        "client-ip-is-proxy-address",
        "actor-ips-blocked-24h",
        "domain-join-failures-24h",
    )
)

EXPECTED_HEALTH_IDS: typing.Final[frozenset[str]] = frozenset(
    (
        "webhook-queue-size",
        "internal-errors-24h",
        "clock-skew-24h",
        "restrained-service-pools",
        "deferred-deletion-stuck",
        "stuck-publications",
        "cluster-nodes-timezones",
        "cluster-node-clock-drift",
        "db-clock-spread",
        "user-services-stuck-preparing",
        "stale-user-services",
        "provider-errors-24h",
        "provider-in-maintenance-mode",
        "db-app-time-mismatch",
        "client-ip-is-proxy-address",
        "actor-ips-blocked-24h",
        "domain-join-failures-24h",
    )
)


class ChecksEndpointTest(rest.test.RESTTestCase):
    @typing.override
    def tearDown(self) -> None:
        # Restore the shipped default root password: config values keep an
        # in-memory copy that outlives the (truncated) test database.
        GlobalConfig.SUPER_USER_PASS.set(consts.security.DEFAULT_SUPERUSER_PASSWORD)
        super().tearDown()

    def _get_report(self, path: str = "system/checks") -> dict[str, typing.Any]:
        response = self.client.rest_get(path)
        self.assertEqual(response.status_code, 200, response.content)
        return typing.cast("dict[str, typing.Any]", response.json())

    def test_anonymous_access_is_denied(self) -> None:
        response = self.client.rest_get("system/checks")
        self.assertEqual(response.status_code, 403, response.content)

    def test_staff_access_is_denied(self) -> None:
        self.login(as_admin=False)
        response = self.client.rest_get("system/checks")
        self.assertEqual(response.status_code, 403, response.content)

    def test_admin_gets_full_report(self) -> None:
        self.login()
        body = self._get_report()
        checks = body["checks"]
        self.assertEqual({check["id"] for check in checks}, EXPECTED_CHECK_IDS)
        # Every check carries its severity, state, category and message
        for check in checks:
            self.assertIn(check["severity"], [severity.value for severity in types.checks.CheckSeverity])
            self.assertIn(check["category"], [category.value for category in types.checks.CheckCategory])
            self.assertIsInstance(check["ok"], bool)
            self.assertTrue(check["message"])
        # Summary counts only failed checks, per severity
        for severity in types.checks.CheckSeverity:
            expected = sum(1 for check in checks if not check["ok"] and check["severity"] == severity.value)
            self.assertEqual(body[severity.value], expected)
        # Category breakdown counts failed checks per category
        for category in types.checks.CheckCategory:
            expected = sum(1 for check in checks if not check["ok"] and check["category"] == category.value)
            self.assertEqual(body["categories"][category.value], expected)

    def test_admin_can_filter_by_category(self) -> None:
        self.login()
        security = self._get_report("system/checks/security")
        health = self._get_report("system/checks/health")

        self.assertEqual(
            {check["id"] for check in security["checks"]}, EXPECTED_CHECK_IDS - EXPECTED_HEALTH_IDS
        )
        self.assertEqual(
            {check["id"] for check in health["checks"]},
            EXPECTED_HEALTH_IDS,
        )
        self.assertTrue(all(check["category"] == "health" for check in health["checks"]))
        self.assertTrue(all(check["category"] == "security" for check in security["checks"]))

    def test_invalid_category_is_rejected(self) -> None:
        self.login()
        response = self.client.rest_get("system/checks/not-a-category")
        self.assertEqual(response.status_code, 400, response.content)

    def test_removed_security_check_alias_is_not_served(self) -> None:
        self.login()
        response = self.client.rest_get("system/security_check")
        self.assertEqual(response.status_code, 400, response.content)

    def test_manual_checks_report_is_empty_for_now(self) -> None:
        # No production manual checks exist yet: the report is valid but empty
        self.login()
        body = self._get_report("system/manual_checks")
        self.assertEqual(body["checks"], [])
        for severity in types.checks.CheckSeverity:
            self.assertEqual(body[severity.value], 0)
        for category in types.checks.CheckCategory:
            self.assertEqual(body["categories"][category.value], 0)

    def test_manual_checks_filter_by_category(self) -> None:
        self.login()
        for category in types.checks.CheckCategory:
            body = self._get_report(f"system/manual_checks/{category.value}")
            self.assertEqual(body["checks"], [])

    def test_manual_checks_requires_admin(self) -> None:
        self.login(as_admin=False)
        response = self.client.rest_get("system/manual_checks")
        self.assertEqual(response.status_code, 403, response.content)

    def test_manual_checks_invalid_category_is_rejected(self) -> None:
        self.login()
        response = self.client.rest_get("system/manual_checks/not-a-category")
        self.assertEqual(response.status_code, 400, response.content)

    def test_admin_report_reflects_configuration_state(self) -> None:
        # Isolate this test from the other CRITICAL/HIGH findings the test
        # settings trigger (DEBUG=True, PROFILING=True, ALLOWED_HOSTS=['*'],
        # CSRF middleware commented out, and the shipped sample SECRET_KEY /
        # RSA_KEY when running off a pristine settings.py). The
        # superuser-credentials check is the one we want to exercise here.
        # The session + request middleware are kept so REST login/auth tokens
        # (which are Django sessions) work inside the overridden settings.
        middleware: list[str] = [
            "django.middleware.security.SecurityMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.middleware.common.CommonMiddleware",
            "uds.middleware.request.GlobalRequestMiddleware",
        ]
        with self.settings(
            DEBUG=False,
            PROFILING=False,
            ALLOWED_HOSTS=["testserver"],
            MIDDLEWARE=middleware,
            SECRET_KEY="a-rotated-not-default-secret-key",
            RSA_KEY="a-rotated-not-default-rsa-key",
        ):
            # Login inside the settings override: the auth token is a Django
            # session signed with the active SECRET_KEY, so it must be issued
            # and validated under the same settings block.
            self.login()
            body = self._get_report("system/checks/security")
            critical = {
                check["id"] for check in body["checks"] if check["severity"] == "critical" and not check["ok"]
            }
            self.assertEqual(critical, {"default-superuser-credentials"})

            # Rotating the root password clears the only critical finding
            GlobalConfig.SUPER_USER_PASS.set("a-rotated-not-default-password")
            body = self._get_report("system/checks/security")
            self.assertEqual(body["critical"], 0)
