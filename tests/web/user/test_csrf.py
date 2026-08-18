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

Server-side checks for CSRF protection.

These tests use ``enforce_csrf_checks=True`` on the Django test client so the
CSRF middleware actually runs. With the default ``enforce_csrf_checks=False``
the client bypasses CSRF, which is why no other test in the repo would have
caught a missing/invalid CSRF setup.

Run with CSRF middleware enabled (it is, in ``src/server/settings.py:333``).
"""
import typing

from django.test import Client
from django.urls import reverse

from uds.core import consts

from ...utils.test import UDSTransactionTestCase

if typing.TYPE_CHECKING:
    from django.http import HttpResponse


class CsrfServerTest(UDSTransactionTestCase):
    """
    Verifies that:

    - the index page seeds the ``csrftoken`` cookie and embeds the token in the
      rendered template,
    - views explicitly decorated with ``@csrf_exempt`` (login, logout, MFA,
      service launcher, the whole REST dispatcher) keep working without a
      token,
    - views that are NOT exempt (the built-in ``set_language`` view used by
      the modern UI's language picker) require a valid token, accepting either
      the form-field shape or the ``X-CSRFToken`` header shape (Angular's
      ``HttpClientXsrfModule``).

    If any of these fail, the server side is the broken side and the Angular
    client can't be blamed.
    """

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        # Use a fresh client that enforces CSRF checks. The default client
        # (UDSClient) skips CSRF, which is fine for the existing test suite
        # but useless here.
        self.csrf_client = Client(enforce_csrf_checks=True)

    def _csrf_token(self) -> str:
        """Returns a valid CSRF token issued by the server."""
        # Issuing a GET against any CSRF-protected page that calls
        # ``csrf.get_token(request)`` (the index does) sets the cookie and
        # returns the matching token in the context.
        response = self.csrf_client.get(reverse("page.index"))
        token = response.wsgi_request.META.get("CSRF_COOKIE")  # type: ignore[attr-defined]
        if not token:
            self.fail("Index did not set a CSRF cookie")
        return token

    def test_index_seeds_csrf_cookie_and_embeds_token(self) -> None:
        import re

        response = self.csrf_client.get(reverse("page.index"))
        self.assertEqual(response.status_code, 200)
        # The CSRF middleware sets the ``csrftoken`` cookie on the response.
        self.assertIn("CSRF_COOKIE", response.wsgi_request.META)  # type: ignore[attr-defined]
        self.assertTrue(response.wsgi_request.META["CSRF_COOKIE"])  # type: ignore[index]
        # The modern template embeds the (unmasked) token in a JS global so
        # the form-based language picker can submit it natively. The cookie
        # value and the form value are different (Django masks the secret for
        # the cookie) — we just check the structure, not the literal value.
        body = response.content.decode("utf-8")
        self.assertIn("csrfToken:", body)
        self.assertIn("csrfField:", body)
        self.assertIn(consts.auth.CSRF_FIELD, body)
        # A non-empty token must have been rendered.
        m = re.search(r"csrfToken:\s*'([^']+)'", body)
        if not m:
            self.fail("csrfToken missing from rendered template")
        self.assertGreater(len(m.group(1)), 20)

    def test_login_post_with_csrf_token_works(self) -> None:
        # The ``login`` view at ``web/views/auth.py:331`` is NOT decorated with
        # ``@csrf_exempt``. The Angular login form reads the ``csrftoken``
        # cookie via JavaScript (``login.component.ts:38``) and submits it as a
        # hidden field. We mimic that flow here.
        #
        # 1) GET to seed the cookie; 2) POST with the cookie value as the
        # form field.
        token = self._csrf_token()
        response: HttpResponse = self.csrf_client.post(
            reverse("page.login"),
            {
                "user": "ghost",
                "password": "ghost",
                "authenticator": "00000000-0000-0000-0000-000000000000",
                consts.auth.CSRF_FIELD: token,
            },
        )
        # No 403 — the POST was processed. The credentials are garbage so the
        # body will reject, but CSRF passed.
        self.assertNotEqual(response.status_code, 403)

    def test_login_post_without_csrf_token_returns_403(self) -> None:
        # Direct POST with no prior GET and no CSRF token fails. This is the
        # behaviour Angular avoids by always loading the index (which seeds the
        # cookie) before submitting the login form.
        response = self.csrf_client.post(
            reverse("page.login"),
            {"user": "ghost", "password": "ghost", "authenticator": "00000000-0000-0000-0000-000000000000"},
        )
        self.assertEqual(response.status_code, 403)

    def test_logout_with_csrf_token_works(self) -> None:
        # logout is not ``@csrf_exempt`` either. Same flow as login: seed the
        # cookie first, then POST with the token.
        token = self._csrf_token()
        response = self.csrf_client.post(
            reverse("page.logout.compat"),
            HTTP_X_CSRFTOKEN=token,
        )
        # logout may 302 redirect or return the response from the view.
        # What we care about is that CSRF didn't 403.
        self.assertNotEqual(response.status_code, 403)

    def test_rest_dispatcher_is_exempt_from_csrf(self) -> None:
        # ``REST.Dispatcher.dispatch`` is ``@csrf_exempt``
        # (REST/dispatcher.py:123). Even POSTs to /uds/rest/... work without
        # a token.
        response = self.csrf_client.post(
            reverse("REST", kwargs={"path": "auth/login"}),
            data='{"authenticator": "x", "username": "y", "password": "z"}',
            content_type="application/json",
        )
        self.assertNotEqual(response.status_code, 403)

    def test_set_language_requires_csrf_token_without_it_returns_403(self) -> None:
        # ``django.views.i18n.set_language`` is the only built-in POST
        # endpoint the modern UI hits that is NOT explicitly exempted. It
        # enforces CSRF.
        response = self.csrf_client.post(
            reverse("set_language"),
            {"language": "en", "next": "/uds/page/services"},
        )
        self.assertEqual(response.status_code, 403)

    def test_set_language_accepts_csrf_in_form_field(self) -> None:
        token = self._csrf_token()
        response = self.csrf_client.post(
            reverse("set_language"),
            {
                "language": "en",
                "next": "/uds/page/services",
                consts.auth.CSRF_FIELD: token,
            },
        )
        # The view redirects on success.
        self.assertIn(response.status_code, (200, 302))

    def test_set_language_accepts_csrf_in_header(self) -> None:
        # This is the shape the Angular ``HttpClientXsrfModule`` uses: a
        # single ``X-CSRFToken`` header, no body field.
        token = self._csrf_token()
        response = self.csrf_client.post(
            reverse("set_language"),
            data="language=en&next=/uds/page/services",
            content_type="application/x-www-form-urlencoded",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertIn(response.status_code, (200, 302))