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
Tests for the WebAuthn (biometric login) views.

Uses synthetic WebAuthn payloads (see tests.utils.webauthn) to exercise:
* biometric_start / biometric_finish (unlock flow)
* save_start / save_finish (registration flow, committed only after login)
* the login POST integration (registration commit + vault rotation)
"""

import json
import typing

from cryptography.hazmat.primitives.asymmetric import ec
from django.urls import reverse

from uds import models
from uds.core import consts
from uds.core import types
from uds.core.managers.crypto.webauthn import b64url_decode, b64url_encode
from uds.core.util.config import GlobalConfig

from ...utils import webauthn as synthetic
from ...utils.web import test


class WebAuthnViewsTest(test.WEBTestCase):
    """
    Test the WebAuthn views with synthetic payloads.
    """

    private_key: ec.EllipticCurvePrivateKey
    # Django test client uses "testserver" as host by default
    rp_id: typing.Final[str] = "testserver"
    origin: typing.Final[str] = "http://testserver"

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        GlobalConfig.ALLOW_BIOMETRIC_AUTH.set(True)
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.user = self.plain_users[0]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def post_json(self, url: str, payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
        response = self.client.post(url, data=json.dumps(payload), content_type="application/json")
        return typing.cast(dict[str, typing.Any], json.loads(response.content.decode("utf-8")))

    def _reloaded_user(self) -> "models.User":
        """Re-fetches the user from the database (fresh properties state)."""
        return models.User.objects.get(id=self.user.id)

    def _register_and_login(self) -> types.webauthn.StoredCredential:
        """Runs the "save biometric credential" flow and a successful login."""
        start = self.post_json(
            reverse("webapi.webauthn.save_start"),
            {"username": self.user.name, "auth_id": self.auth.uuid},
        )
        payload = synthetic.registration_payload(
            self.private_key, self.rp_id, self.origin, b64url_decode(start["challenge"])
        )
        result = self.post_json(reverse("webapi.webauthn.save_finish"), payload)
        self.assertTrue(result.get("ok"), f"save_finish failed: {result}")

        # Login POST (with the webauthn payload field, as the client would do)
        response = self.client.post(
            reverse("page.login"),
            {
                "user": self.user.name,
                "password": self.user.name,
                "authenticator": self.auth.uuid,
            },
        )
        self.assertEqual(response.status_code, 302, "Login with pending registration should redirect")

        credential = self._reloaded_user().webauthn_credentials.all()[0]
        self.assertEqual(credential.credential_id, b64url_encode(b"\x01" * 32))
        self.assertEqual(credential.vault, payload["vault"])
        return credential

    def _unlock(self) -> dict[str, typing.Any]:
        """Runs the biometric unlock flow, returning the finish response."""
        start = self.post_json(
            reverse("webapi.webauthn.biometric_start"),
            {"username": self.user.name, "auth_id": self.auth.uuid},
        )
        self.assertTrue(start.get("available"), f"biometric_start not available: {start}")
        payload = synthetic.assertion_payload(
            self.private_key, self.rp_id, self.origin, b64url_decode(start["challenge"]), sign_count=2
        )
        return self.post_json(reverse("webapi.webauthn.biometric_finish"), payload)

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_biometric_start_disabled(self) -> None:
        GlobalConfig.ALLOW_BIOMETRIC_AUTH.set(False)
        response = self.client.post(
            reverse("webapi.webauthn.biometric_start"),
            data=json.dumps({"username": self.user.name, "auth_id": self.auth.uuid}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_biometric_start_unknown_user(self) -> None:
        result = self.post_json(
            reverse("webapi.webauthn.biometric_start"),
            {"username": "nonexistent", "auth_id": self.auth.uuid},
        )
        self.assertEqual(result, {"available": False})

    def test_biometric_start_without_vault(self) -> None:
        result = self.post_json(
            reverse("webapi.webauthn.biometric_start"),
            {"username": self.user.name, "auth_id": self.auth.uuid},
        )
        self.assertEqual(result, {"available": False})

    def test_save_requires_vault(self) -> None:
        start = self.post_json(
            reverse("webapi.webauthn.save_start"),
            {"username": self.user.name, "auth_id": self.auth.uuid},
        )
        payload = synthetic.registration_payload(
            self.private_key, self.rp_id, self.origin, b64url_decode(start["challenge"])
        )
        del payload["vault"]
        result = self.post_json(reverse("webapi.webauthn.save_finish"), payload)
        self.assertIn("error", result)

    def test_registration_committed_on_successful_login(self) -> None:
        self._register_and_login()

    def test_registration_discarded_on_failed_login(self) -> None:
        start = self.post_json(
            reverse("webapi.webauthn.save_start"),
            {"username": self.user.name, "auth_id": self.auth.uuid},
        )
        payload = synthetic.registration_payload(
            self.private_key, self.rp_id, self.origin, b64url_decode(start["challenge"])
        )
        result = self.post_json(reverse("webapi.webauthn.save_finish"), payload)
        self.assertTrue(result.get("ok"), f"save_finish failed: {result}")

        # Failed login: pending registration is discarded
        self.do_login(self.user.name, "wrong password", self.auth.uuid)
        self.assertFalse(self._reloaded_user().webauthn_credentials)

    def test_unfinished_registration_ignored_on_login(self) -> None:
        # save_start without save_finish (prompt cancelled or timed out)
        self.post_json(
            reverse("webapi.webauthn.save_start"),
            {"username": self.user.name, "auth_id": self.auth.uuid},
        )
        response = self.client.post(
            reverse("page.login"),
            {
                "user": self.user.name,
                "password": self.user.name,
                "authenticator": self.auth.uuid,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self._reloaded_user().webauthn_credentials)
        self.assertNotIn(consts.webauthn.SESSION_SAVE_DATA, self.client.session)

    def test_unlock_flow(self) -> None:
        self._register_and_login()
        result = self._unlock()
        self.assertTrue(result.get("ok"), f"biometric_finish failed: {result}")
        self.assertEqual(result.get("username"), self.user.name)
        self.assertEqual(result.get("auth_id"), self.auth.uuid)
        stored = self._reloaded_user().webauthn_credentials.all()[0]
        self.assertEqual(result.get("blob"), stored.vault)

    def test_unlock_rotation_applied_on_login(self) -> None:
        credential = self._register_and_login()
        previous_salt = credential.prf_salt
        result = self._unlock()
        self.assertTrue(result.get("ok"))

        # Unlock done, now login succeeds with the rotated vault
        rotated_vault = b64url_encode(b"rotated-vault-blob")
        response = self.client.post(
            reverse("page.login"),
            {
                "user": self.user.name,
                "password": self.user.name,
                "authenticator": self.auth.uuid,
                "webauthn": json.dumps({"rotated_vault": rotated_vault}),
            },
        )
        self.assertEqual(response.status_code, 302)

        credential = self._reloaded_user().webauthn_credentials.all()[0]
        self.assertEqual(credential.vault, rotated_vault)
        self.assertEqual(credential.generation, 1)
        self.assertNotEqual(credential.prf_salt, previous_salt)

    def test_unlock_invalidated_on_failed_login(self) -> None:
        self._register_and_login()
        result = self._unlock()
        self.assertTrue(result.get("ok"))

        # Unlock was verified, but the login failed: the vault is stale
        # (password changed at backend?) so the credential is invalidated
        self.do_login(self.user.name, "wrong password", self.auth.uuid)
        self.assertFalse(self._reloaded_user().webauthn_credentials)
