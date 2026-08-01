# -*- coding: utf-8 -*-
#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
"""
Tests for the REST ``/auth/login`` handler focused on the bearer token
scheme introduced by the ``ses-`` prefix migration.

Notes
-----
* ``/auth/login`` is ANONYMOUS, so the test client must NOT be logged-in.
* We use a fresh UDSClient (not the one from ``self.client`` of
  RESTTestCase, which keeps headers state across tests) to avoid mixing
  Authorization headers between cases.
"""

from __future__ import annotations

import typing

from uds.core import consts

from tests.utils import rest
from tests.utils.test import UDSClient


class LoginBearerTokenTest(rest.test.RESTTestCase):
    """``/auth/login`` returns a ``ses-``-prefixed token, and the token
    works through both the legacy ``X-Auth-Token`` header and the new
    ``Authorization: Bearer ses-...`` header.
    """

    PATH = "auth/login"

    def _fresh_client(self) -> UDSClient:
        """Return a brand-new UDSClient with no pre-set headers.

        ``self.client`` is shared across tests of a single class and
        carries whatever headers previous tests left behind; here we
        want a clean slate for each assertion.
        """
        return UDSClient()

    def _do_login(self) -> tuple[UDSClient, typing.Any]:
        """Perform a fresh login using a clean client.

        Returns ``(client, login_response_json)``.
        """
        client = self._fresh_client()
        # Pick the admin created by RESTTestCase.setUp (which already
        # called ``self.login()`` on its own self.client).  We log in
        # again against a separate client because /auth/login is
        # ANONYMOUS and we want to assert the body shape independently
        # of any existing session.
        response = client.post(
            self.compose_rest_url("auth/login"),
            data={
                "auth_id": self.auth.uuid,
                "username": self.admins[0].name,
                "password": self.admins[0].name,
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        return client, response.json()

    def compose_rest_url(self, path: str) -> str:
        # ``UDSClient.compose_rest_url`` is an instance method; replicate
        # it here for our fresh client to avoid coupling to the test's
        # shared ``self.client``.
        return f"/uds/rest/{path}"

    # ------------------------------------------------------------------
    # Behaviour of ``/auth/login``
    # ------------------------------------------------------------------
    def test_login_returns_ses_prefixed_token(self) -> None:
        """The ``token`` field of a successful login response is a
        ``ses-``-prefixed string (scheme tag) followed by the bare
        session key.
        """
        _, body = self._do_login()
        self.assertEqual(body["result"], "ok")
        self.assertIsNotNone(body.get("token"))
        self.assertTrue(body["token"].startswith(consts.auth.SESSION_KEY_PREFIX))

    def test_login_token_works_via_x_auth_token_legacy_header(self) -> None:
        """Feeding the login token back via the legacy
        ``X-Auth-Token`` header (with or without the ``ses-`` prefix)
        grants access.  Handler strips the prefix before lookup.
        """
        client, body = self._do_login()
        token = body["token"]
        # Use it as-is (prefixed) - exact behaviour for clients that
        # just parrot back whatever login returned.
        client.add_header(consts.auth.AUTH_TOKEN_HEADER, token)
        # Hit a small known-anonymous endpoint via a fresh path that
        # does NOT require auth, to confirm the token is well-formed.
        # Use /auth/logout which is Role.USER; a valid session reaches
        # ``get_auth_token`` and clears it, returning {"result":"ok"}.
        r = client.get(self.compose_rest_url("auth/logout"))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json(), {"result": "ok"})

    def test_login_token_works_via_authorization_bearer_header(self) -> None:
        """Feeding the login token through ``Authorization: Bearer
        ses-...`` (the modern path) also grants access.  The token
        must be re-emitted verbatim with the ``ses-`` prefix to
        validate via the bearer dispatch.
        """
        client, body = self._do_login()
        token = body["token"]
        # Sanity: token still starts with the prefix.
        self.assertTrue(token.startswith(consts.auth.SESSION_KEY_PREFIX))
        client.add_header("Authorization", f"Bearer {token}")
        # Same fresh client now has no X-Auth-Token; the Authorization
        # header alone must be enough.
        r = client.get(self.compose_rest_url("auth/logout"))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json(), {"result": "ok"})

    def test_legacy_bare_token_still_accepted(self) -> None:
        """A bare session key (no ``ses-`` prefix) supplied via
        ``X-Auth-Token`` keeps working for backward compatibility.

        Performs a normal login to obtain a real session key, strips
        the ``ses-`` prefix, and uses the raw value.
        """
        client, body = self._do_login()
        token = body["token"]
        bare = token.removeprefix(consts.auth.SESSION_KEY_PREFIX)
        self.assertNotEqual(bare, token)  # sanity: prefix really was there
        client.add_header(consts.auth.AUTH_TOKEN_HEADER, bare)
        r = client.get(self.compose_rest_url("auth/logout"))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json(), {"result": "ok"})
