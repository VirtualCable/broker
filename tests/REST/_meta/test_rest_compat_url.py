#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
# Redistribution and use in source and binary code, with or without modification,
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
"""
Tests for the legacy ``/rest/<path>`` URL prefix.

Pre-5.0 clients (e.g. UCC) call the REST API at the legacy ``/rest/`` prefix
without the ``uds/`` segment that 5.0 added. The compat route is declared in
``src/uds/urls.py`` and dispatches to the same ``REST.Dispatcher`` view as the
canonical ``/uds/rest/`` route.

Bug B2 (fixed): the compat route captured the path segment with the kwarg
name ``arguments``, but ``Dispatcher.dispatch()`` only declares ``path``.
Calling any legacy endpoint returned 500 ``TypeError: Dispatcher.dispatch()
got an unexpected keyword argument 'arguments'`` before this fix landed.

These tests freeze both URL prefixes against the dispatcher contract so the
regression cannot be reintroduced silently.
"""
from __future__ import annotations

import json

from tests.utils import rest
from tests.utils.test import UDSClient


# Legacy compat prefix (pre-5.0). Must remain a working route.
LEGACY_REST_PATH = "/rest/"

# Canonical 5.0 prefix. Kept here as a sanity baseline.
CANONICAL_REST_PATH = "/uds/rest/"


class LegacyCompatUrlTest(rest.test.RESTTestCase):
    """The legacy ``/rest/`` URL prefix must dispatch to the same handlers
    as the canonical ``/uds/rest/`` prefix, without raising ``TypeError``
    on the missing ``arguments`` kwarg."""

    def _fresh_client(self) -> UDSClient:
        """A clean client with no leftover headers from other tests."""
        return UDSClient()

    # ------------------------------------------------------------------
    # Anonymous endpoint via the legacy prefix
    # ------------------------------------------------------------------
    def test_legacy_version_endpoint_returns_200(self) -> None:
        """``GET /rest/version`` must dispatch to UDSVersion and return 200.

        Pre-fix this raised ``TypeError`` (500). The endpoint is anonymous,
        so no auth headers are required.
        """
        client = self._fresh_client()
        response = client.get(f"{LEGACY_REST_PATH}version")
        self.assertEqual(response.status_code, 200, response.content)
        body = json.loads(response.content)
        self.assertIn("version", body)
        self.assertIn("build", body)

    # ------------------------------------------------------------------
    # Canonical prefix keeps working (regression guard for the rename)
    # ------------------------------------------------------------------
    def test_canonical_version_endpoint_returns_200(self) -> None:
        """``GET /uds/rest/version`` must continue to dispatch correctly."""
        client = self._fresh_client()
        response = client.get(f"{CANONICAL_REST_PATH}version")
        self.assertEqual(response.status_code, 200, response.content)

    # ------------------------------------------------------------------
    # POST /auth/login via the legacy prefix must not raise TypeError
    # ------------------------------------------------------------------
    def test_legacy_login_post_does_not_500(self) -> None:
        """``POST /rest/auth/login`` must dispatch to Login (anonymous).

        Pre-fix this raised ``TypeError: Dispatcher.dispatch() got an
        unexpected keyword argument 'arguments'`` (500 Internal Server Error).
        Login accepts an empty body and returns ``{"result": "error"}``;
        what matters here is that the dispatcher reaches the handler.
        """
        client = self._fresh_client()
        response = client.post(
            f"{LEGACY_REST_PATH}auth/login",
            data={},
            content_type="application/json",
        )
        self.assertNotEqual(
            response.status_code,
            500,
            f"Legacy /rest/auth/login must not 500 (was: {response.content!r})",
        )
        body = json.loads(response.content)
        # Whatever the login says (success or not), we got a JSON body, which
        # means the handler ran end-to-end and did not crash in routing.
        self.assertIn("result", body)

    def test_canonical_login_post_does_not_500(self) -> None:
        """``POST /uds/rest/auth/login`` mirrors the legacy case as a guard
        against regressions introduced while wiring the compat route."""
        client = self._fresh_client()
        response = client.post(
            f"{CANONICAL_REST_PATH}auth/login",
            data={},
            content_type="application/json",
        )
        self.assertNotEqual(response.status_code, 500, response.content)
        body = json.loads(response.content)
        self.assertIn("result", body)

    # ------------------------------------------------------------------
    # Sub-path under the legacy prefix: <handler>/<method> with non-empty path
    # ------------------------------------------------------------------
    def test_legacy_subpath_dispatches_correctly(self) -> None:
        """Sub-paths under ``/rest/`` must also reach the dispatcher.

        Hits ``/rest/auth/auths`` (anonymous, lists authenticators).
        Pre-fix this raised ``TypeError`` (500).
        """
        client = self._fresh_client()
        response = client.get(f"{LEGACY_REST_PATH}auth/auths")
        self.assertEqual(response.status_code, 200, response.content)
        # The endpoint returns a JSON array of authenticators (possibly empty).
        self.assertIsInstance(json.loads(response.content), list)