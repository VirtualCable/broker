#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright notice,
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
Author: Janier Rodríguez, jrodriguez at virtualcable dot es

Contracts for SAMLAuthenticator.logout_callback redirect targets.

The single logout callback is served through ``auth_callback_stage2``, which only
honors absolute http(s) targets, so every url returned here must survive
``uds.web.views.auth._safe_redirect_url`` untouched.
"""

import typing
from unittest import mock

from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory
from django.urls import reverse

from tests.utils.test import UDSTestCase
from uds.auths.SAML.saml import SAMLAuthenticator
from uds.core import types
from uds.core.environment import Environment
from uds.web.views.auth import _safe_redirect_url

CALLBACK_PATH: typing.Final[str] = "/uds/authcallback/saml?logout=true"


class SAMLLogoutCallbackTest(UDSTestCase):
    def _request(self) -> typing.Any:
        request = typing.cast(typing.Any, RequestFactory().get(CALLBACK_PATH, HTTP_HOST="broker.example.net"))
        request.session = SessionStore()
        request.user = None
        return request

    def _logout_callback(self, slo_url: str | None) -> types.auth.AuthenticationResult:
        request = self._request()
        auth = mock.Mock()
        auth.process_slo.return_value = slo_url
        auth.get_errors.return_value = []

        with Environment.temporary_environment() as env:
            instance = SAMLAuthenticator(environment=env)
            with (
                mock.patch.object(instance, "build_onelogin_settings", return_value={}),
                mock.patch("uds.auths.SAML.saml.OneLogin_Saml2_Settings"),
                mock.patch("uds.auths.SAML.saml.OneLogin_Saml2_Auth", return_value=auth),
            ):
                return instance.logout_callback({"post_data": {}, "get_data": {}}, request)

    def test_terminal_slo_redirects_to_an_absolute_login_url(self) -> None:
        """process_slo returns None when the IdP leaves nowhere else to go."""
        result = self._logout_callback(None)

        self.assertEqual(result.success, types.auth.AuthenticationState.REDIRECT)
        self.assertEqual(result.url, f"http://broker.example.net{reverse('page.login')}")

    def test_terminal_slo_target_is_accepted_by_the_callback_view(self) -> None:
        url = typing.cast(str, self._logout_callback(None).url)

        self.assertEqual(_safe_redirect_url(url), url)

    def test_idp_target_is_forwarded_untouched(self) -> None:
        """A logout chain still in progress belongs to the IdP, not to us."""
        idp_url = "https://idp.example.com/saml/sls?SAMLResponse=abc"
        result = self._logout_callback(idp_url)

        self.assertEqual(result.url, idp_url)
        self.assertEqual(_safe_redirect_url(idp_url), idp_url)

    def test_slo_clears_the_session(self) -> None:
        request = self._request()
        request.session["samlLogoutRequestId"] = "req-id"
        auth = mock.Mock()
        auth.process_slo.return_value = None
        auth.get_errors.return_value = []

        with Environment.temporary_environment() as env:
            instance = SAMLAuthenticator(environment=env)
            with (
                mock.patch.object(instance, "build_onelogin_settings", return_value={}),
                mock.patch("uds.auths.SAML.saml.OneLogin_Saml2_Settings"),
                mock.patch("uds.auths.SAML.saml.OneLogin_Saml2_Auth", return_value=auth),
            ):
                instance.logout_callback({"post_data": {}, "get_data": {}}, request)

        auth.process_slo.assert_called_once_with(request_id="req-id")
        self.assertNotIn("samlLogoutRequestId", request.session)
