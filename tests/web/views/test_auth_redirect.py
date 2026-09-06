#
# Copyright (c) 2025 Virtual Cable S.L.
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
Contracts for uds.web.views.auth._safe_redirect_url.

Targets returned by authenticators (OAuth2, SAML, ...) through ``Redirect`` /
``Logout`` exceptions are authoritative: a SAML logout, for example, is a chain
of redirects across the IdP and other service providers, so the destination
host must not be second-guessed. The only contract: the target must be an
absolute http(s) URL. Anything else falls back to ``/``.
"""

from tests.utils.test import UDSTestCase
from uds.web.views import auth as auth_views


class SafeRedirectUrlTest(UDSTestCase):
    def test_absolute_https_url_to_any_host_is_kept(self) -> None:
        # SAML SLO: the logout chain continues on the IdP (or any other SP)
        target = "https://idp.example.com/saml/sls?SigAlg=rsa-sha256&SAMLResponse=abc"
        self.assertEqual(auth_views._safe_redirect_url(target), target)

    def test_absolute_http_url_to_any_host_is_kept(self) -> None:
        target = "http://idp.example.com/logout"
        self.assertEqual(auth_views._safe_redirect_url(target), target)

    def test_absolute_url_with_port_is_kept(self) -> None:
        target = "https://idp.example.com:8443/slo"
        self.assertEqual(auth_views._safe_redirect_url(target), target)

    def test_empty_target_falls_back_to_root(self) -> None:
        self.assertEqual(auth_views._safe_redirect_url(""), "/")

    def test_relative_path_falls_back_to_root(self) -> None:
        # Local routes make no sense on federated redirects, forbidden by design
        self.assertEqual(auth_views._safe_redirect_url("/index"), "/")

    def test_plain_string_falls_back_to_root(self) -> None:
        self.assertEqual(auth_views._safe_redirect_url("not-a-url"), "/")

    def test_protocol_relative_url_falls_back_to_root(self) -> None:
        # Not absolute (no scheme), so rejected
        self.assertEqual(auth_views._safe_redirect_url("//evil.com/steal"), "/")

    def test_javascript_scheme_falls_back_to_root(self) -> None:
        self.assertEqual(auth_views._safe_redirect_url("javascript:alert(1)"), "/")

    def test_data_scheme_falls_back_to_root(self) -> None:
        self.assertEqual(auth_views._safe_redirect_url("data:text/html,<script>alert(1)</script>"), "/")
