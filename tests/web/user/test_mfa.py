# -*- coding: utf-8 -*-
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
"""

import typing

from django.urls import reverse

from uds.core import types
from uds.core.util.config import GlobalConfig

from ...fixtures import authenticators as fixtures_authenticators
from ...fixtures import mfas as mfa_fixtures
from ...utils.web import test

if typing.TYPE_CHECKING:
    from django.http import HttpResponse
    from ...utils.test import UDSHttpResponse


class WebMFATest(test.WEBTestCase):
    """
    Test MFA web flow
    """

    def setUp(self) -> None:
        super().setUp()
        # Create an MFA provider and link to the authenticator
        self.mfa = mfa_fixtures.create_db_mfa()
        self.auth.mfa = self.mfa
        self.auth.save()

        # InternalDB authenticator reads mfa_data from user model
        self.user = self.plain_users[0]
        self.user.mfa_data = "test@example.com"
        self.user.save()

    def test_redirect_when_not_authenticated(self) -> None:
        response: "HttpResponse" = self.client.get(reverse("page.mfa"))
        self.assertRedirects(response, reverse("page.index"), status_code=302, fetch_redirect_response=False)

    def test_redirect_when_already_authorized(self) -> None:
        # First login normally (will redirect to MFA)
        self.do_login(self.user.name, self.user.name, self.auth.uuid)
        # GET MFA and submit valid code to authorize
        self.client.get(reverse("page.mfa"))
        self.client.post(reverse("page.mfa"), {"code": "123456", "remember": False})
        # Now authorized, accessing MFA should redirect to index
        response: "HttpResponse" = self.client.get(reverse("page.mfa"))
        self.assertRedirects(response, reverse("page.index"), status_code=302, fetch_redirect_response=False)

    def test_redirect_when_no_mfa_provider(self) -> None:
        self.auth.mfa = None
        self.auth.save()
        self.do_login(self.user.name, self.user.name, self.auth.uuid, check=True)

    def test_mfa_flow_get_shows_form(self) -> None:
        # Login triggers redirect to MFA
        response = self.do_login(self.user.name, self.user.name, self.auth.uuid)
        self.assertRedirects(response, reverse("page.mfa"), status_code=302, fetch_redirect_response=False)
        # GET MFA page
        response = self.client.get(reverse("page.mfa"))
        self.assertEqual(response.status_code, 200)
        # Session must contain MFA data
        self.assertIn("mfa", self.client.session)
        self.assertEqual(self.client.session["mfa"]["label"], "Test Code")

    def test_mfa_valid_code(self) -> None:
        # Login and get MFA page to trigger process()
        self.do_login(self.user.name, self.user.name, self.auth.uuid)
        self.client.get(reverse("page.mfa"))
        # POST valid code
        response = self.client.post(reverse("page.mfa"), {"code": "123456", "remember": False})
        self.assertRedirects(response, reverse("page.index"), status_code=302, fetch_redirect_response=False)
        # Verify authorization
        response = self.client.get(reverse("page.index"))
        self.assertEqual(response.status_code, 200)

    def test_mfa_invalid_code(self) -> None:
        self.do_login(self.user.name, self.user.name, self.auth.uuid)
        self.client.get(reverse("page.mfa"))
        # POST invalid code
        response = self.client.post(reverse("page.mfa"), {"code": "wrong"})
        self.assertRedirects(
            response,
            reverse("page.error", kwargs={"err": types.errors.Error.INVALID_MFA_CODE}),
            status_code=302,
            fetch_redirect_response=False,
        )
        self.assertIn("mfa_tries", self.client.session)
        self.assertEqual(self.client.session["mfa_tries"], 1)

    def test_mfa_too_many_tries(self) -> None:
        self.do_login(self.user.name, self.user.name, self.auth.uuid)
        self.client.get(reverse("page.mfa"))
        max_tries = GlobalConfig.MAX_LOGIN_TRIES.as_int()
        response: "UDSHttpResponse" = None  # type: ignore[assignment]
        for _ in range(max_tries):
            response = self.client.post(reverse("page.mfa"), {"code": "wrong"})

        # Last attempt redirects to access denied
        self.assertRedirects(
            response,
            reverse("page.error", kwargs={"err": types.errors.Error.ACCESS_DENIED}),
            status_code=302,
            fetch_redirect_response=False,
        )
        # Session should be flushed
        self.assertNotIn("mfa_tries", self.client.session)

    def test_mfa_timeout(self) -> None:
        self.do_login(self.user.name, self.user.name, self.auth.uuid)
        # Set MFA start time far in the past
        session = self.client.session
        session["mfa_start_time"] = 0
        session.save()
        # GET MFA page should detect timeout and redirect to login
        response = self.client.get(reverse("page.mfa"))
        self.assertRedirects(response, reverse("page.login"), status_code=302, fetch_redirect_response=False)
        # Session should be flushed
        self.assertNotIn("mfa_start_time", self.client.session)

    def test_mfa_remember_device(self) -> None:
        # Create an MFA with remember_device > 0
        mfa_with_remember = mfa_fixtures.create_db_mfa(remember_device=24)
        self.auth.mfa = mfa_with_remember
        self.auth.save()
        self.do_login(self.user.name, self.user.name, self.auth.uuid)
        self.client.get(reverse("page.mfa"))
        # POST with remember=True
        response = self.client.post(reverse("page.mfa"), {"code": "123456", "remember": True})
        self.assertRedirects(response, reverse("page.index"), status_code=302, fetch_redirect_response=False)
        # Cookie should be set
        self.assertIn("mfa_status", response.cookies)

    def test_mfa_remember_device_skips_mfa_on_relogin(self) -> None:
        mfa_with_remember = mfa_fixtures.create_db_mfa(remember_device=24)
        self.auth.mfa = mfa_with_remember
        self.auth.save()
        # First login: complete MFA with remember=True
        self.do_login(self.user.name, self.user.name, self.auth.uuid)
        self.client.get(reverse("page.mfa"))
        self.client.post(reverse("page.mfa"), {"code": "123456", "remember": True})
        # Logout
        self.client.get(reverse("page.logout"))
        # Login again
        response = self.do_login(self.user.name, self.user.name, self.auth.uuid)
        self.assertRedirects(response, reverse("page.mfa"), status_code=302, fetch_redirect_response=False)
        # GET MFA -> should skip due to cookie and redirect to index
        response = self.client.get(reverse("page.mfa"))
        self.assertRedirects(response, reverse("page.index"), status_code=302, fetch_redirect_response=False)

    def test_mfa_skip_mfa_group_skips_mfa(self) -> None:
        group_with_skip = fixtures_authenticators.create_db_groups(self.auth, number_of_groups=1)[0]
        group_with_skip.skip_mfa = types.states.State.ACTIVE
        group_with_skip.save()
        self.user.groups.add(group_with_skip)
        # Login should NOT redirect to MFA (goes straight to index)
        self.do_login(self.user.name, self.user.name, self.auth.uuid, check=True)

    def test_mfa_no_identifier_allowed(self) -> None:
        # User without mfa_data: MFA will allow login because allow_login_without_identifier returns True
        user_no_id = self.plain_users[1]
        user_no_id.mfa_data = ""
        user_no_id.save()
        # Login redirects to MFA
        response = self.do_login(user_no_id.name, user_no_id.name, self.auth.uuid)
        self.assertRedirects(response, reverse("page.mfa"), status_code=302, fetch_redirect_response=False)
        # GET MFA page -> should authorize directly due to empty identifier
        response = self.client.get(reverse("page.mfa"))
        self.assertRedirects(response, reverse("page.index"), status_code=302, fetch_redirect_response=False)
