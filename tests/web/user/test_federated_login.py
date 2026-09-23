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
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import typing
from unittest import mock

from django.http import HttpResponse
from django.urls import reverse

from uds.core import types
from uds.models import TicketStore

from ...fixtures import authenticators as fixtures_authenticators
from ...utils.web import test


class FederatedLoginTest(test.WEBTestCase):
    """
    Test the SSO callback flow keeps the session authorized on the next request
    """

    def test_callback_stage2_keeps_session_authorized(self) -> None:
        auth = fixtures_authenticators.create_db_authenticator()
        user = fixtures_authenticators.create_db_users(auth, number_of_users=1)[0]

        params = types.auth.AuthCallbackParams(
            https=False,
            host="testserver",
            path=f"/uds/page/auth/callback/{auth.small_name}",
            port="80",
            get_params=typing.cast(typing.Any, {}),
            post_params=typing.cast(typing.Any, {}),
            query_string="",
        )
        ticket = TicketStore.create({"params": params, "auth": auth.uuid})

        with mock.patch(
            "uds.web.views.auth.authenticate_via_callback",
            return_value=types.auth.LoginResult(user=user),
        ):
            response = typing.cast(
                "HttpResponse",
                self.client.get(reverse("page.auth.callback_stage2", args=[ticket])),
            )

        self.assertRedirects(response, reverse("page.index"), status_code=302)

        response = typing.cast("HttpResponse", self.client.get("/uds/utility/uds.js"))
        self.assertContains(response, f'"user": "{user.name}"', status_code=200)
