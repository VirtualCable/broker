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
Tests for the WebAuthn (biometric) custom methods of the Users detail REST
handler: ``biometric_credentials`` (listing) and ``revoke_biometric``.
"""

import typing

from uds import models
from uds.core import types
from uds.core.util import webauthn_credentials

from tests.utils import rest


class WebAuthnCustomMethodsTest(rest.test.RESTTestCase):
    """
    Functional tests for the biometric credentials custom methods.
    """

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def _credential(self, credential_id: str) -> types.webauthn.StoredCredential:
        """Synthetic stored credential (values are opaque to the server)."""
        return types.webauthn.StoredCredential(
            credential_id=credential_id,
            rp_id="uds.example.com",
            user_handle="user-handle",
            aaguid="",
            public_key="cose-key",
            sign_count=0,
            prf_salt="salt",
            vault="vault-blob",
            generation=0,
            created=webauthn_credentials.now_epoch(),
            last_used=None,
        )

    def _credentials_url(self, user: models.User) -> str:
        return f"authenticators/{self.auth.uuid}/users/{user.uuid}"

    def _user(self) -> models.User:
        # users is a plain list (admins + staffs + plain), annotate for mypy
        user = self.users[0]
        return user

    def test_biometric_credentials_lists_registered(self) -> None:
        user = self._user()
        user.webauthn_credentials.add(self._credential("cred-1"))
        user.webauthn_credentials.add(self._credential("cred-2"))

        response = self.client.rest_get(f"{self._credentials_url(user)}/biometric_credentials")
        self.assertEqual(response.status_code, 200, response.content)
        listed = typing.cast("list[dict[str, typing.Any]]", response.json())
        self.assertEqual(len(listed), 2)
        self.assertIn("cred-1", {c["credential_id"] for c in listed})
        self.assertIn("cred-2", {c["credential_id"] for c in listed})

    def test_biometric_credentials_empty(self) -> None:
        user = self._user()
        response = self.client.rest_get(f"{self._credentials_url(user)}/biometric_credentials")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), [])

    def test_revoke_biometric_removes_all(self) -> None:
        user = self._user()
        user.webauthn_credentials.add(self._credential("cred-1"))
        user.webauthn_credentials.add(self._credential("cred-2"))

        response = self.client.rest_post(f"{self._credentials_url(user)}/revoke_biometric")
        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["revoked"], 2)
        self.assertFalse(user.webauthn_credentials)

    def test_revoke_biometric_without_credentials(self) -> None:
        user = self._user()
        response = self.client.rest_post(f"{self._credentials_url(user)}/revoke_biometric")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["revoked"], 0)
