# -*- coding: utf-8 -*-
#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
"""
Tests for the security mechanisms of the REST ``/auth/login`` handler:

* Superuser ("root") login is only reachable when superuser web access is
  enabled (``SUPER_USER_ALLOW_WEBACCESS``) and the request comes from a
  trusted source (``TRUSTED_SOURCES``).
* Failed superuser logins are throttled (sleep) and accounted on the per-IP
  fail cache, which ends up blocking the client after too many failures.
* Login parameters are sanitized before being written to the logs, so
  credentials never reach the log files.

Notes
-----
* ``/auth/login`` is ANONYMOUS, so the test clients must NOT be logged-in;
  a fresh ``UDSClient`` is used for every request.
"""

from __future__ import annotations

import typing
import unittest

from unittest import mock

from uds.core import consts
from uds.core.util.cache import Cache
from uds.core.util.config import GlobalConfig
from uds.REST.methods import login_logout

from tests.utils import rest
from tests.utils.test import UDSClient

if typing.TYPE_CHECKING:
    from tests.utils.test import UDSHttpResponse

SUPERUSER_AUTH_ID = "00000000-0000-0000-0000-000000000000"
SUPERUSER_PASSWORD = "test-root-password"  # nosec: hardcoded test password
DEFAULT_ROOT_PASSWORD = "udsmam0"  # nosec: hardcoded test password
TEST_CLIENT_IP = "127.0.0.1"  # REMOTE_ADDR used by UDSClient on ipv4


class SanitizeLoginParamsTest(unittest.TestCase):
    """Unit tests for the ``_sanitize_login_params`` helper."""

    def test_none_returns_empty_dict(self) -> None:
        self.assertEqual(login_logout._sanitize_login_params(None), {})

    def test_empty_mapping_returns_empty_dict(self) -> None:
        self.assertEqual(login_logout._sanitize_login_params({}), {})

    def test_credential_fields_are_removed(self) -> None:
        sanitized = login_logout._sanitize_login_params(
            {
                "username": "user",
                "auth_id": "auth-uuid",
                "password": "the-password",
                "passwd": "the-passwd",
                "token": "the-token",
                "secret": "the-secret",
            }
        )
        self.assertEqual(sanitized, {"username": "user", "auth_id": "auth-uuid"})

    def test_original_mapping_is_not_modified(self) -> None:
        params: dict[str, str] = {"username": "user", "password": "the-password"}
        login_logout._sanitize_login_params(params)
        self.assertEqual(params, {"username": "user", "password": "the-password"})


class SuperuserLoginSecurityTest(rest.test.RESTTestCase):
    """Security controls for the superuser login on REST ``/auth/login``."""

    superuser: str

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.superuser = GlobalConfig.SUPER_USER_LOGIN.get(True)
        GlobalConfig.SUPER_USER_PASS.set(SUPERUSER_PASSWORD)
        GlobalConfig.SUPER_USER_ALLOW_WEBACCESS.set(True)
        GlobalConfig.TRUSTED_SOURCES.set("*")

    @typing.override
    def tearDown(self) -> None:
        # Restore the defaults: config values keep an in-memory copy that
        # outlives the (truncated) test database and would leak into other
        # tests running on the same process.
        GlobalConfig.SUPER_USER_ALLOW_WEBACCESS.set(True)
        GlobalConfig.TRUSTED_SOURCES.set("*")
        GlobalConfig.SUPER_USER_PASS.set(DEFAULT_ROOT_PASSWORD)
        super().tearDown()

    def _superuser_login(self, password: str, username: str | None = None) -> "UDSHttpResponse":
        client = UDSClient()
        return client.post(
            client.compose_rest_url("auth/login"),
            data={
                "auth_id": SUPERUSER_AUTH_ID,
                "username": username if username is not None else self.superuser,
                "password": password,
            },
            content_type="application/json",
        )

    def test_superuser_login_succeeds_when_allowed_and_trusted(self) -> None:
        response = self._superuser_login(SUPERUSER_PASSWORD)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["result"], "ok", body)
        self.assertTrue(body["token"].startswith(consts.auth.SESSION_KEY_PREFIX), body)

        # And the returned token grants access as the root user
        client = UDSClient()
        client.add_header(consts.auth.AUTH_TOKEN_HEADER, body["token"])
        response = client.get(client.compose_rest_url("auth/logout"))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["result"], "ok", response.content)

    def test_superuser_login_denied_when_web_access_disabled(self) -> None:
        GlobalConfig.SUPER_USER_ALLOW_WEBACCESS.set(False)
        with mock.patch("uds.REST.methods.login_logout.time.sleep"):  # Keep the test fast
            response = self._superuser_login(SUPERUSER_PASSWORD)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["result"], "error", body)
        self.assertIsNone(body.get("token"), body)

    def test_superuser_login_denied_from_untrusted_source(self) -> None:
        # The test client sends from 127.0.0.1; make it untrusted
        GlobalConfig.TRUSTED_SOURCES.set("10.0.0.0/8")
        with mock.patch("uds.REST.methods.login_logout.time.sleep"):  # Keep the test fast
            response = self._superuser_login(SUPERUSER_PASSWORD)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["result"], "error", body)
        self.assertIsNone(body.get("token"), body)

    def test_failed_superuser_login_sleeps_and_counts_fails(self) -> None:
        fail_cache = Cache("RESTapi")
        self.assertIsNone(fail_cache.get(TEST_CLIENT_IP))
        with mock.patch("uds.REST.methods.login_logout.time.sleep") as sleep_mock:
            response = self._superuser_login("wrong-password")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["result"], "error", response.content)
        # Online guessing is throttled...
        sleep_mock.assert_called_once_with(3)
        # ...and the failure is accounted on the per-IP fail cache
        self.assertEqual(fail_cache.get(TEST_CLIENT_IP), 1)

    def test_client_is_blocked_after_too_many_failed_superuser_logins(self) -> None:
        fail_cache = Cache("RESTapi")
        with mock.patch("uds.REST.methods.login_logout.time.sleep"):  # Keep the test fast
            for expected_fails in range(1, consts.system.ALLOWED_FAILS + 2):
                response = self._superuser_login("wrong-password")
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.json()["result"], "error", response.content)
                self.assertEqual(fail_cache.get(TEST_CLIENT_IP), expected_fails)

            # Even the right credentials are rejected while the IP is blocked
            response = self._superuser_login(SUPERUSER_PASSWORD)
        self.assertEqual(response.status_code, 403, response.content)

    def test_credentials_are_not_logged_on_login_failure(self) -> None:
        secret = "super-secret-password"
        client = UDSClient()
        with self.assertLogs("uds.REST.methods.login_logout", level="ERROR") as captured:
            # Missing "username" forces the error-logging path, carrying the password on the params
            response = client.post(
                client.compose_rest_url("auth/login"),
                data={"auth_id": self.auth.uuid, "password": secret},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["result"], "error", response.content)
        output = "\n".join(captured.output)
        self.assertNotIn(secret, output)
        # Non-sensitive parameters are still logged
        self.assertIn("auth_id", output)
