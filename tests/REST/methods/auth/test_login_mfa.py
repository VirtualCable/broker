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
# FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
# OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#

"""
Tests for the MFA protection on the direct REST login: a user protected by
MFA cannot obtain a REST session token through ``POST /uds/rest/auth/login``
(the MFA challenge is a web-only flow). Users in a ``skip_mfa`` group are
not affected.
"""

import json
import typing

from uds import models
from uds.core import consts, types

from tests.fixtures import mfas as mfa_fixtures
from tests.utils.rest import test as rest_test


class LoginMfaTest(rest_test.RESTTestCase):
    """Direct REST login must be denied for MFA-protected users."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.mfa = mfa_fixtures.create_db_mfa()
        self.auth.mfa = self.mfa
        self.auth.save()
        # InternalDB reads the MFA identifier from the user mfa_data field
        self.user = self.plain_users[0]
        self.user.mfa_data = "mfa-user@example.com"
        self.user.save()

    def _rest_login(self, user: models.User) -> dict[str, typing.Any]:
        response = self.client.rest_post(
            "auth/login",
            data=json.dumps(
                {
                    "auth_id": self.auth.uuid,
                    "username": user.name,
                    "password": user.name,  # fixture users: password == username
                }
            ).encode("utf-8"),
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_mfa_user_rest_login_is_denied(self) -> None:
        result = self._rest_login(self.user)
        self.assertEqual(result["result"], "error", result)
        self.assertIn("MFA", result.get("error", ""))

    def test_mfa_user_rest_login_does_not_issue_token(self) -> None:
        result = self._rest_login(self.user)
        # No session token must be issued in a denied response
        self.assertIsNone(result.get("token"), result)

    def test_mfa_user_in_skip_mfa_group_can_login(self) -> None:
        group_with_skip = self.groups[0]
        group_with_skip.skip_mfa = types.states.State.ACTIVE
        group_with_skip.save()
        self.user.groups.add(group_with_skip)
        result = self._rest_login(self.user)
        self.assertEqual(result["result"], "ok", result)
        self.assertTrue(result["token"].startswith(consts.auth.SESSION_KEY_PREFIX), result)

    def test_login_without_mfa_provider_is_not_affected(self) -> None:
        self.auth.mfa = None
        self.auth.save()
        result = self._rest_login(self.user)
        self.assertEqual(result["result"], "ok", result)
        self.assertTrue(result["token"].startswith(consts.auth.SESSION_KEY_PREFIX), result)
