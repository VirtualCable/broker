#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
"""
Tests for the REST path scope mechanism (``allowed_paths`` user property).

Covers the pure helpers (path normalization, scope sanitization, scope
matching) and the end-to-end enforcement at the dispatcher level: users
carrying the ``allowed_paths`` property can only reach the REST paths
listed in it, no matter if they authenticate through a session token or
an API token.

Path scopes can only be assigned through the user API token issuance
endpoint (admin only): they are a property of the user, so a scoped user
cannot lift its own restriction by logging in again.
"""

from __future__ import annotations

import json

from django.core.management import CommandError, call_command

from uds import models
from uds.core import consts
from uds.core.util.config import GlobalConfig
from uds.core.util import rest_paths as rest_utils
from uds.REST.utils import sanitize_rest_scopes

from tests.utils import rest
from tests.utils.test import UDSClient


class RestPathHelpersTest(rest.test.RESTTestCase):
    """Pure-function tests for the path scope helpers."""

    def test_normalize_rest_path(self) -> None:
        cases: dict[str, str] = {
            "mcp": "mcp",
            "/mcp/": "mcp",
            "uds/rest/mcp": "mcp",
            "rest/mcp": "mcp",
            "uds/mcp": "mcp",
            "UDS/REST/MCP": "mcp",
            "users": "users",
            "": "",
            "uds/rest": "",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(rest_utils.normalize_rest_path(source), expected)

    def test_sanitize_rest_scopes(self) -> None:
        self.assertEqual(sanitize_rest_scopes(["/uds/rest/mcp/", "mcp"]), ["mcp"])
        self.assertEqual(sanitize_rest_scopes(["providers", "users"]), ["providers", "users"])
        self.assertEqual(sanitize_rest_scopes([]), [])
        with self.assertRaises(Exception):
            sanitize_rest_scopes("mcp")
        with self.assertRaises(Exception):
            sanitize_rest_scopes(["mcp", 42])

    def test_rest_path_allowed(self) -> None:
        # Prefixes in the payload are normalized away
        self.assertTrue(rest_utils.rest_path_allowed("mcp", ["uds/rest/mcp"]))
        # Segment boundary prefix matching
        self.assertTrue(rest_utils.rest_path_allowed("providers/xxx/services", ["providers"]))
        self.assertFalse(rest_utils.rest_path_allowed("providers2", ["providers"]))
        # No scopes = nothing allowed
        self.assertFalse(rest_utils.rest_path_allowed("mcp", []))
        self.assertFalse(rest_utils.rest_path_allowed("auth/logout", ["mcp"]))


class AllowedPathsDispatchTest(rest.test.RESTTestCase):
    """End-to-end dispatcher enforcement of the ``allowed_paths`` property."""

    def _fresh_client(self) -> UDSClient:
        return UDSClient()

    def _login(self, client: UDSClient, user: models.User) -> None:
        """Plain login for ``user`` on ``client`` (no scope parameters)."""
        response = client.post(
            "/uds/rest/auth/login",
            data={
                "auth_id": self.auth.uuid,
                "username": user.name,
                "password": user.name,
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["result"], "ok", body)
        client.add_header(consts.auth.AUTH_TOKEN_HEADER, body["token"])

    def test_login_ignores_scope_payload(self) -> None:
        """Login carries no scope parameters: a user cannot touch its own scopes."""
        user = self.admins[1]
        with user.properties as props:
            props["allowed_paths"] = "mcp"  # Invalid payload, must stay untouched
        client = self._fresh_client()
        response = client.post(
            "/uds/rest/auth/login",
            data={
                "auth_id": self.auth.uuid,
                "username": user.name,
                "password": user.name,
                "allowed_paths": [],  # If honored, it would lift the restriction
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        # Login succeeded but did not modify the stored value
        self.assertEqual(user.properties.get("allowed_paths"), "mcp")

    def test_unrestricted_user_reaches_any_path(self) -> None:
        user = self.admins[1]
        client = self._fresh_client()
        self._login(client, user)
        response = client.get("/uds/rest/authenticators", content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)

    def test_admin_session_ignores_scopes(self) -> None:
        """Admin GUI authenticates through sessions: scopes never apply there.

        This is the anti-lockout rule: even if an admin carries path
        scopes (targeted to its API tokens), its session keeps full access.
        """
        user = self.admins[1]
        user.rest_allowed_paths = ["mcp"]
        client = self._fresh_client()
        self._login(client, user)
        response = client.get("/uds/rest/authenticators", content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)

    def test_admin_api_token_is_scoped(self) -> None:
        """User API tokens are always scoped, even for admins."""
        user = self.admins[1]
        GlobalConfig.MCP_ENABLED.set(True)
        user.rest_allowed_paths = ["mcp"]
        self.login_with_api_token(user=user)
        response = self.client.get("/uds/rest/authenticators", content_type="application/json")
        self.assertEqual(response.status_code, 403, response.content)

    def test_staff_session_is_scoped(self) -> None:
        """Only admins are exempt on session logins; staff sessions obey scopes."""
        user = self.staffs[0]
        client = self._fresh_client()
        self._login(client, user)
        response = client.get("/uds/rest/authenticators", content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        user.rest_allowed_paths = ["mcp"]
        response = client.get("/uds/rest/authenticators", content_type="application/json")
        self.assertEqual(response.status_code, 403, response.content)

    def test_scoped_user_reaches_scoped_path(self) -> None:
        user = self.staffs[0]
        user.rest_allowed_paths = ["authenticators"]
        client = self._fresh_client()
        self._login(client, user)
        response = client.get("/uds/rest/authenticators", content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)

    def test_scope_matches_segment_prefix(self) -> None:
        user = self.admins[1]
        user.rest_allowed_paths = ["authenticators"]
        self.login_with_api_token(user=user)
        # List and detail (deeper segments) are allowed
        response = self.client.get("/uds/rest/authenticators", content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        response = self.client.get(
            f"/uds/rest/authenticators/{self.auth.uuid}", content_type="application/json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        # Other trees are not
        response = self.client.get("/uds/rest/providers", content_type="application/json")
        self.assertEqual(response.status_code, 403, response.content)

    def test_empty_scopes_remove_previous_restriction(self) -> None:
        user = self.staffs[0]
        user.rest_allowed_paths = ["mcp"]
        client = self._fresh_client()
        self._login(client, user)
        self.assertEqual(
            client.get("/uds/rest/authenticators", content_type="application/json").status_code,
            403,
        )
        user.rest_allowed_paths = None
        self.assertEqual(user.rest_allowed_paths, [])
        self.assertEqual(
            client.get("/uds/rest/authenticators", content_type="application/json").status_code,
            200,
        )

    def test_mcp_scoped_user_reaches_mcp_path(self) -> None:
        user = self.admins[1]
        GlobalConfig.MCP_ENABLED.set(True)
        user.rest_allowed_paths = ["mcp"]
        self.login_with_api_token(user=user)
        self.assertEqual(user.rest_allowed_paths, ["mcp"])
        # The MCP endpoint must be fully usable with the scoped token
        response = self.client.rest_post(
            "mcp", data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode("utf-8")
        )
        self.assertEqual(response.status_code, 200, response.content)


class AllowedPathsTokenEndpointTest(rest.test.RESTTestCase):
    """Scopes are assigned through the user API token endpoint (admin only)."""

    def _token_url(self, user: models.User) -> str:
        return f"/uds/rest/authenticators/{self.auth.uuid}/users/{user.uuid}/token"

    def test_token_endpoint_sets_scopes(self) -> None:
        self.login(self.admins[0])
        user = self.staffs[0]
        response = self.client.post(
            self._token_url(user),
            data={"allowed_paths": ["mcp"]},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(user.rest_allowed_paths, ["mcp"])
        # Now the scoped user cannot use its API token outside "mcp"
        raw_token = response.json()["token"]
        client = UDSClient()
        client.add_header(consts.auth.AUTHORIZATION_HEADER, f"Bearer {raw_token}")
        self.assertEqual(
            client.get("/uds/rest/authenticators", content_type="application/json").status_code,
            403,
        )

    def test_token_endpoint_clears_scopes(self) -> None:
        self.login(self.admins[0])
        user = self.staffs[0]
        user.rest_allowed_paths = ["mcp"]
        response = self.client.post(
            self._token_url(user),
            data={"allowed_paths": []},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(user.rest_allowed_paths, [])

    def test_token_endpoint_invalid_scopes(self) -> None:
        self.login(self.admins[0])
        user = self.staffs[0]
        response = self.client.post(
            self._token_url(user),
            data={"allowed_paths": "mcp"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)


class ResetUserRestCommandTest(rest.test.RESTTestCase):
    """The recovery management command clears path scopes."""

    def _scopes(self, user: models.User) -> None:
        user.rest_allowed_paths = ["mcp"]

    def test_clears_scopes_of_named_users(self) -> None:
        scoped, untouched = self.staffs[0], self.staffs[1]
        self._scopes(scoped)
        self._scopes(untouched)
        call_command("reset_user_rest", scoped.name)
        self.assertEqual(scoped.rest_allowed_paths, [])
        self.assertEqual(untouched.rest_allowed_paths, ["mcp"])

    def test_clears_scopes_by_uuid(self) -> None:
        scoped = self.admins[1]
        self._scopes(scoped)
        call_command("reset_user_rest", scoped.uuid)
        self.assertEqual(scoped.rest_allowed_paths, [])

    def test_clears_all_scopes(self) -> None:
        for user in (self.staffs[0], self.admins[1]):
            self._scopes(user)
        call_command("reset_user_rest", "--all")
        self.assertEqual(self.staffs[0].rest_allowed_paths, [])
        self.assertEqual(self.admins[1].rest_allowed_paths, [])

    def test_missing_user_fails(self) -> None:
        with self.assertRaises(CommandError):
            call_command("reset_user_rest", "nonexistent-user")

    def test_requires_arguments(self) -> None:
        with self.assertRaises(CommandError):
            call_command("reset_user_rest")
