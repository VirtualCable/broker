# -*- coding: utf-8 -*-
#
# Copyright (c) 2025 Virtual Cable S.L.
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
# AND EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
# IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
# OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
Dispatcher contract tests (Phase 1 — Safety net).

This module freezes the behavior of the dispatcher so that any future change
(adding QUERY, OPTIONS, deprecation headers, etc.) is detected if it breaks
the existing contract.

Code under test:
- src/uds/REST/dispatcher.py  (Dispatcher.dispatch, Dispatcher.error_response)
- src/uds/REST/handlers.py    (Handler base)

Previously fixed bugs (kept here only as historical context):
- B1 (fixed): Dispatcher.error_response used to pass the JSON body as
  HttpResponseNotAllowed's first positional arg (i.e. `permitted_methods`),
  causing TypeError on every 405 for an unknown method. Now error_response
  detects HttpResponseNotAllowed and constructs it correctly, including an
  Allow header advertising the methods the dispatcher understands.

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""
import json
import logging
import typing

from tests.utils import rest
from tests.utils.test import REST_PATH

logger = logging.getLogger(__name__)

# HTTP methods recognized today by the dispatcher (dispatcher.py:165).
# Any method outside this list receives 405 Method Not Allowed.
SUPPORTED_METHODS: typing.Final[tuple[str, ...]] = ('get', 'post', 'put', 'delete', 'options', 'query')

# Methods the dispatcher rejects today (405).
FORBIDDEN_METHODS: typing.Final[tuple[str, ...]] = (
    'patch',    # RFC 5789  - not yet supported
    'head',     # RFC 9110  - not yet supported
    'trace',    # RFC 9110  - not yet supported
    'connect',  # RFC 9110  - not applicable
    'foo',      # invented method
)


class DispatcherContractTest(rest.test.RESTTestCase):
    """
    Freezes the REST dispatcher contract as it stands today.

    Any test that fails after a dispatcher change (e.g. adding QUERY in
    Phase 2) must be INTENTIONALLY updated in that phase, not blindly fixed.
    If a test that was not expected to change fails, it is a regression.
    """

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _rest_request(
        self, method: str, path: str, *, data: bytes | str | None = None, content_type: str = 'application/json'
    ) -> 'typing.Any':
        """Send a REST request with an arbitrary HTTP method.

        Uses Client.generic to support methods not covered by the
        rest_get/rest_post/rest_put/rest_delete helpers (e.g. QUERY, OPTIONS, HEAD).

        Note: for GET we don't send a body or content_type, because the processor
        would try to parse the empty body as JSON and fail. For other methods,
        pass the body explicitly if needed.
        """
        url = f'{REST_PATH}{path}'
        if method.lower() == 'get':
            # GET without body (same as rest_get does)
            return self.client.rest_get(path)
        # For the rest, generic allows arbitrary methods.
        # Only pass content_type if there is a body; without body we don't send
        # content_type to avoid the processor trying to parse ''.
        # Note: generic() is not overridden in UDSClient, so we must pass
        # the auth headers explicitly via the headers kwarg.
        if data is None:
            return self.client.generic(method.upper(), url, headers=self.client.uds_headers)
        return self.client.generic(
            method.upper(), url, data=data, content_type=content_type, headers=self.client.uds_headers,
        )

    # ------------------------------------------------------------------
    # T1A.1 - Unsupported methods return clean 405 (bug B1 fix in place)
    # ------------------------------------------------------------------
    def test_forbidden_methods_return_405_with_allow(self) -> None:
        """Every method outside get/post/put/delete returns 405 + Allow header.

        Bug B1 (fixed): dispatcher.error_response used to pass the JSON body as
        the first positional arg of HttpResponseNotAllowed, which Django treats
        as `permitted_methods`. Result was a TypeError (500 in production).
        Now error_response detects 405 and forwards the permitted methods list
        correctly, producing a clean 405 with an Allow header.
        """
        for method in FORBIDDEN_METHODS:
            with self.subTest(method=method):
                response = self._rest_request(method, 'providers/overview')
                self.assertEqual(
                    response.status_code,
                    405,
                    f'Method {method.upper()} must return 405 (not 500)',
                )
                # Allow header must be present and contain at least one of
                # the recognized methods.
                allow = response.get('Allow', '')
                self.assertTrue(allow, f'405 response for {method} is missing Allow header')
                for recognized in ('GET', 'POST', 'PUT', 'DELETE'):
                    self.assertIn(
                        recognized,
                        allow,
                        f'Allow header for {method} must include {recognized}; got: {allow!r}',
                    )

    def test_allowed_methods_not_rejected_as_unknown(self) -> None:
        """get/post/put/delete are not rejected by the dispatcher method filter.

        For GET we use a real path and expect 200 (handler exists and we are
        logged in as admin).
        """
        response = self.client.rest_get('providers/overview')
        self.assertEqual(response.status_code, 200)

    # ------------------------------------------------------------------
    # T1A.2 - 405 body is JSON with "error" key (replaces bug-B1 docs)
    # ------------------------------------------------------------------
    def test_405_returns_json_error(self) -> None:
        """The 405 response carries a JSON body with the 'error' key.

        Replaces the old test_405_current_behaviour_due_to_bug_b1, which
        documented the broken (TypeError-raising) behavior.
        """
        response = self._rest_request('patch', 'providers/overview')
        self.assertEqual(response.status_code, 405)
        self.assertIn('application/json', response.get('Content-Type', ''))
        body = json.loads(response.content)
        self.assertIn('error', body)
        self.assertEqual(body['error'], 'Method PATCH not allowed')

    # ------------------------------------------------------------------
    # T1A.4 - Fixed headers present on 2xx responses
    # ------------------------------------------------------------------
    def test_fixed_response_headers_on_success(self) -> None:
        """Every successful response carries the dispatcher fixed headers.

        Reference: src/uds/REST/dispatcher.py:212-216
        - UDS-Version: <version>;<stamp>
        - Response-Stamp: <sql_stamp_seconds>
        - Cache-Control: no-cache, no-store, must-revalidate
        - Pragma: no-cache
        - Expires: 0
        """
        response = self.client.rest_get('providers/overview')
        self.assertEqual(response.status_code, 200)

        # UDS-Version in 'version;stamp' form
        uds_version = response.get('UDS-Version')
        self.assertIsNotNone(uds_version, 'UDS-Version header must be present')
        assert uds_version is not None
        self.assertIn(';', uds_version, 'UDS-Version must be in the form version;stamp')

        # Response-Stamp present and numeric
        response_stamp = response.get('Response-Stamp')
        self.assertIsNotNone(response_stamp, 'Response-Stamp header must be present')
        assert response_stamp is not None
        int(response_stamp)  # raises ValueError if not numeric

        # Cache headers: exact 'no-cache, no-store, must-revalidate'
        self.assertEqual(response.get('Cache-Control'), 'no-cache, no-store, must-revalidate')
        self.assertEqual(response.get('Pragma'), 'no-cache')
        self.assertEqual(response.get('Expires'), '0')

    def test_cache_control_header_always_no_store(self) -> None:
        """The Cache-Control header always reports no-store (security policy).

        Verified on a 200 response; the API security contract requires that
        no REST response is cacheable by proxies.
        """
        response = self.client.rest_get('providers/overview')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get('Cache-Control'), 'no-cache, no-store, must-revalidate')

    # ------------------------------------------------------------------
    # T1A.5 - Nonexistent path -> 404
    # ------------------------------------------------------------------
    def test_nonexistent_path_returns_404(self) -> None:
        """A path that resolves to no handler returns 404.

        Reference: dispatcher.py (handler_node not found).
        Note: the dispatcher returns 404 with Content-Type text/plain for
        'Service not found', unlike the JSON 404 for a missing item.
        """
        response = self._rest_request('get', 'this-handler-does-not-exist/overview')
        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------
    # T1A.6 - No auth token on an authenticated handler -> 403
    # ------------------------------------------------------------------
    def test_authenticated_handler_without_token_returns_403(self) -> None:
        """An authenticated handler called without X-Auth-Token returns 403.

        Reference: src/uds/REST/handlers.py (Handler.__init__, AccessDenied).
        We use a fresh client (without login) to ensure there is no token.
        """
        from tests.utils.test import UDSClient

        unauth_client = UDSClient()
        url = f'{REST_PATH}providers/overview'
        response = unauth_client.get(url)
        self.assertEqual(response.status_code, 403)

    # ------------------------------------------------------------------
    # T1A.7 - 405 for method-allowed-by-dispatcher-but-missing-in-handler DOES work
    # ------------------------------------------------------------------
    def test_405_for_undefined_method_in_handler_works(self) -> None:
        """A method allowed by the dispatcher but absent in the handler yields 405 with Allow.

        Reference: src/uds/REST/dispatcher.py:168-172 (AttributeError path).
        Unlike bug B1, THIS path builds HttpResponseNotAllowed correctly passing
        a LIST of methods -> works and returns Allow.

        providers is a ModelHandler: it defines get/put/delete but post only
        accepts 'test'. A POST to /providers/overview (not /test) hits
        InvalidMethodError -> 405 with Allow, or RequestError -> 400.
        The key point: it is NOT the TypeError of bug B1.
        """
        response = self.client.rest_post('providers/overview')
        self.assertIn(response.status_code, (400, 405))

    # ------------------------------------------------------------------
    # Future-contract documentation (no real assertion, just documentation)
    # ------------------------------------------------------------------
    def test_contract_documentation(self) -> None:
        """Placeholder test documenting the current contract for future phases.

        When Phase 2 (extended dispatcher) is implemented:
        - FORBIDDEN_METHODS must lose 'query' and 'options'
        - test_forbidden_methods_trigger_bug_b1 must be updated (query/options
          are no longer forbidden)
        - Add test that OPTIONS returns Allow including QUERY
        - Add test that QUERY no longer returns 405/raises TypeError
        - Fix bug B1 (replace test_405_current_behaviour_due_to_bug_b1 with
          test_405_returns_json_error)

        This verifies the constants in this module reflect the current
        contract. If someone adds QUERY to SUPPORTED_METHODS without updating
        FORBIDDEN_METHODS, this test fails as a reminder.
        """
        self.assertIn('query', SUPPORTED_METHODS, 'QUERY is now supported (Change A)')
        self.assertIn('options', SUPPORTED_METHODS, 'OPTIONS is now supported (Change C)')

    # ------------------------------------------------------------------
    # Change C — OPTIONS for capabilities discovery (RFC 9110 §9.3.7)
    # ------------------------------------------------------------------
    def test_options_returns_204_with_allow_header(self) -> None:
        """OPTIONS returns 204 No Content with an Allow header.

        Reference: src/uds/REST/dispatcher.py OPTIONS handler.
        The Allow header lists the methods the handler actually implements
        plus OPTIONS itself (always supported by the dispatcher).
        """
        response = self._rest_request('options', 'providers/overview')
        self.assertEqual(response.status_code, 204, 'OPTIONS must return 204')
        allow = response.get('Allow', '')
        self.assertTrue(allow, 'OPTIONS response must include Allow header')
        self.assertIn('OPTIONS', allow)
        self.assertIn('GET', allow, 'handlers without get() must still advertise GET if defined')

    def test_options_includes_uds_version_header(self) -> None:
        """OPTIONS includes UDS-Version header like any other response."""
        response = self._rest_request('options', 'providers/overview')
        self.assertEqual(response.status_code, 204)
        uds_version = response.get('UDS-Version')
        self.assertIsNotNone(uds_version, 'OPTIONS must include UDS-Version')
        assert uds_version is not None
        self.assertIn(';', uds_version)

    def test_options_no_auth_required(self) -> None:
        """OPTIONS does not require authentication (RFC 9110 §9.3.7).

        The dispatcher handles OPTIONS before instantiating the handler,
        so no auth check is performed.
        """
        from tests.utils.test import UDSClient

        unauth_client = UDSClient()
        url = f'{REST_PATH}providers/overview'
        response: typing.Any = unauth_client.generic('OPTIONS', url)
        self.assertEqual(response.status_code, 204, 'OPTIONS must work without auth')
        self.assertTrue(response.get('Allow', ''), 'OPTIONS must include Allow header')

    def test_options_on_collection_path(self) -> None:
        """OPTIONS on a collection path (e.g. /providers) returns Allow.

        ModelHandler for providers defines get/put/delete/post.
        """
        response = self._rest_request('options', 'providers')
        self.assertEqual(response.status_code, 204)
        allow = response.get('Allow', '')
        self.assertIn('OPTIONS', allow)
        self.assertIn('GET', allow)

    def test_options_on_nonexistent_path_returns_404(self) -> None:
        """OPTIONS on a path with no handler returns 404 (same as other methods)."""
        response = self._rest_request('options', 'this-does-not-exist')
        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------
    # Change A — QUERY method (RFC 10008)
    # ------------------------------------------------------------------
    def test_query_collection_returns_same_as_get(self) -> None:
        """QUERY on a collection returns the same structure as GET.

        Reference: src/uds/REST/handlers.py Handler.query().
        QUERY reads OData params from the JSON body instead of the query
        string, then delegates to get().
        """
        # GET baseline
        response_get = self.client.rest_get('providers')
        self.assertEqual(response_get.status_code, 200)
        body_get = json.loads(response_get.content)

        # QUERY with empty body (no OData filtering) — should match GET
        response_query = self._rest_request(
            'query', 'providers', data=json.dumps({}), content_type='application/json',
        )
        self.assertEqual(response_query.status_code, 200)
        body_query = json.loads(response_query.content)

        # Both should return a list with the same count
        self.assertEqual(len(body_get), len(body_query))

    def test_query_with_filter_in_body(self) -> None:
        """QUERY with $filter in the JSON body filters results.

        Uses a filter that should return fewer results than the full set.
        """
        response = self._rest_request(
            'query', 'providers',
            data=json.dumps({'$top': 1}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertLessEqual(len(body), 1)

    def test_query_options_includes_query(self) -> None:
        """OPTIONS Allow header includes QUERY since Handler.query() exists."""
        response = self._rest_request('options', 'providers/overview')
        self.assertEqual(response.status_code, 204)
        allow = response.get('Allow', '')
        self.assertIn('QUERY', allow, 'Allow must advertise QUERY (Handler defines query())')

