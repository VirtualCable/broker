#
# Copyright (c) 2024 Virtual Cable S.L.
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
Author: Adolfo Gomez, dkmaster at dkmon dot com
"""

from unittest import mock

from django.http.request import QueryDict

from tests.utils.test import UDSTestCase
from uds.auths.X509Certificate.authenticator import _subject_to_mapping
from uds.auths.X509Certificate.authenticator import _verify_cert_signed_by_ca
from uds.core import exceptions
from uds.core import types
from uds.core.types.auth import AuthTypeGroup
from uds.models import TicketStore

from . import fixtures


class TestHelpers(UDSTestCase):
    """Tests for the module-level helper functions."""

    def test_verify_rsa_valid(self) -> None:
        fix = fixtures.make_rsa_fixture()
        self.assertTrue(_verify_cert_signed_by_ca(fix.client_cert, fix.ca_cert))

    def test_verify_rsa_wrong_ca(self) -> None:
        fix = fixtures.make_rsa_fixture()
        wrong = fixtures.make_rsa_fixture(ca_cn="Wrong CA")
        self.assertFalse(_verify_cert_signed_by_ca(fix.client_cert, wrong.ca_cert))

    def test_verify_ec_valid(self) -> None:
        fix = fixtures.make_ec_fixture()
        self.assertTrue(_verify_cert_signed_by_ca(fix.client_cert, fix.ca_cert))

    def test_verify_ec_wrong_ca(self) -> None:
        fix = fixtures.make_ec_fixture()
        wrong = fixtures.make_ec_fixture(ca_cn="Wrong EC CA")
        self.assertFalse(_verify_cert_signed_by_ca(fix.client_cert, wrong.ca_cert))

    def test_subject_to_mapping(self) -> None:
        fix = fixtures.make_rsa_fixture(client_cn="testuser_mapping")
        mapping = _subject_to_mapping(fix.client_cert.subject)
        self.assertIn("CN", mapping)
        self.assertEqual(mapping["CN"], ["testuser_mapping"])

    def test_subject_to_mapping_multivalue(self) -> None:
        from cryptography.x509 import Name
        from cryptography.x509 import NameAttribute
        from cryptography.x509.oid import NameOID

        multi = Name(
            [
                NameAttribute(NameOID.COMMON_NAME, "user"),
                NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "eng"),
                NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "dev"),
            ]
        )
        mapping = _subject_to_mapping(multi)
        self.assertEqual(mapping["CN"], ["user"])
        self.assertEqual(mapping["OU"], ["eng", "dev"])


class TestAuthenticatorInitialization(UDSTestCase):
    """Tests for the authenticator's initialize() method."""

    def test_valid_ca(self) -> None:
        fix = fixtures.make_rsa_fixture()
        with fixtures.create_authenticator(fix) as instance:
            self.assertEqual(instance.auth_type_group, AuthTypeGroup.CERTIFICATE)
            self.assertEqual(instance.ca_certificate.value, fix.ca_pem)

    def test_invalid_pem(self) -> None:
        fix = fixtures.make_rsa_fixture()
        with self.assertRaises(exceptions.ui.ValidationError):
            with fixtures.create_authenticator(fix) as instance:
                instance.ca_certificate.value = "not a valid pem"
                instance.initialize({"name": "test"})

    def test_non_ca_certificate(self) -> None:
        """A certificate without CA:TRUE in BasicConstraints should fail."""
        fix = fixtures.make_rsa_fixture()
        with self.assertRaises(exceptions.ui.ValidationError):
            with fixtures.create_authenticator(fix) as instance:
                # Use client cert instead of CA cert
                instance.ca_certificate.value = fix.client_pem
                instance.initialize({"name": "test"})

    def test_invalid_username_regex(self) -> None:
        fix = fixtures.make_rsa_fixture()
        with self.assertRaises(exceptions.ui.ValidationError):
            with fixtures.create_authenticator(fix) as instance:
                instance.username_attr.value = "CN=(INVALID[regex"
                instance.initialize({"name": "test"})


TEST_AUTH_UUID: str = "test-auth-uuid-for-x509"


class TestAuthCallback(UDSTestCase):
    """Tests for the authenticator's auth_callback() method."""

    def _make_params(self, cert_pem: str, ticket_id: str = "") -> types.auth.AuthCallbackParams:
        """Build AuthCallbackParams with an encrypted bridge payload."""
        payload = fixtures.encrypt_payload(cert_pem, ticket_id=ticket_id)
        post_params = QueryDict("", mutable=True)
        post_params["payload"] = payload
        return types.auth.AuthCallbackParams(
            https=True,
            host="test",
            path="/",
            port="443",
            get_params=QueryDict(),
            post_params=post_params,
            query_string="",
        )

    def _create_ticket(self) -> str:
        """Create a valid replay-protection ticket for testing."""
        return TicketStore.create(
            {"nonce": True},
            validity=300,
            owner=TEST_AUTH_UUID,
            secure=True,
        )

    def test_valid_cert_rsa(self) -> None:
        fix = fixtures.make_rsa_fixture(client_cn="rsauser")
        with mock.patch.object(fixtures.X509CertificateAuthenticator, "get_uuid", return_value=TEST_AUTH_UUID):
            with fixtures.create_authenticator(fix) as instance:
                ticket_id = self._create_ticket()
                params = self._make_params(fix.client_pem, ticket_id=ticket_id)
                gm = mock.MagicMock()
                request = mock.MagicMock()

                result = instance.auth_callback(params, gm, request)
                self.assertEqual(result.success, types.auth.AuthenticationState.SUCCESS)
                self.assertEqual(result.username, "rsauser")

                # Verify get_real_name works
                realname = instance.get_real_name("rsauser")
                self.assertEqual(realname, "rsauser")

                # Verify groups
                gm.validate.assert_called_once()
                groups_arg = gm.validate.call_args[0][0]
                self.assertIn("x509_users", groups_arg)

    def test_valid_cert_ec(self) -> None:
        fix = fixtures.make_ec_fixture(client_cn="ecuser")
        with mock.patch.object(fixtures.X509CertificateAuthenticator, "get_uuid", return_value=TEST_AUTH_UUID):
            with fixtures.create_authenticator(fix) as instance:
                ticket_id = self._create_ticket()
                params = self._make_params(fix.client_pem, ticket_id=ticket_id)
                gm = mock.MagicMock()
                request = mock.MagicMock()

                result = instance.auth_callback(params, gm, request)
                self.assertEqual(result.success, types.auth.AuthenticationState.SUCCESS)
                self.assertEqual(result.username, "ecuser")

    def test_no_cert_data(self) -> None:
        fix = fixtures.make_rsa_fixture()
        with fixtures.create_authenticator(fix) as instance:
            params = types.auth.AuthCallbackParams(
                https=True,
                host="test",
                path="/",
                port="443",
                get_params=QueryDict(),
                post_params=QueryDict(),  # no 'payload'
                query_string="",
            )
            gm = mock.MagicMock()
            request = mock.MagicMock()
            result = instance.auth_callback(params, gm, request)
            self.assertEqual(result.success, types.auth.AuthenticationState.FAIL)

    def test_wrong_ca(self) -> None:
        fix = fixtures.make_rsa_fixture()
        wrong = fixtures.make_rsa_fixture(ca_cn="Other CA")
        with mock.patch.object(fixtures.X509CertificateAuthenticator, "get_uuid", return_value=TEST_AUTH_UUID):
            with fixtures.create_authenticator(fix) as instance:
                ticket_id = self._create_ticket()
                params = self._make_params(wrong.client_pem, ticket_id=ticket_id)
                gm = mock.MagicMock()
                request = mock.MagicMock()
                result = instance.auth_callback(params, gm, request)
                self.assertEqual(result.success, types.auth.AuthenticationState.FAIL)

    def test_trusted_issuer_match(self) -> None:
        fix = fixtures.make_rsa_fixture(client_cn="issuer_user")
        with mock.patch.object(fixtures.X509CertificateAuthenticator, "get_uuid", return_value=TEST_AUTH_UUID):
            with fixtures.create_authenticator(fix, trusted_issuer=fix.client_issuer_dn) as instance:
                ticket_id = self._create_ticket()
                params = self._make_params(fix.client_pem, ticket_id=ticket_id)
                gm = mock.MagicMock()
                request = mock.MagicMock()
                result = instance.auth_callback(params, gm, request)
                self.assertEqual(result.success, types.auth.AuthenticationState.SUCCESS)
                self.assertEqual(result.username, "issuer_user")

    def test_trusted_issuer_mismatch(self) -> None:
        fix = fixtures.make_rsa_fixture(client_cn="no_match")
        with mock.patch.object(fixtures.X509CertificateAuthenticator, "get_uuid", return_value=TEST_AUTH_UUID):
            with fixtures.create_authenticator(fix, trusted_issuer="CN=Wrong Issuer") as instance:
                ticket_id = self._create_ticket()
                params = self._make_params(fix.client_pem, ticket_id=ticket_id)
                gm = mock.MagicMock()
                request = mock.MagicMock()
                result = instance.auth_callback(params, gm, request)
                self.assertEqual(result.success, types.auth.AuthenticationState.FAIL)

    def test_username_extraction_custom_regex(self) -> None:
        fix = fixtures.make_rsa_fixture(client_cn="john.doe")
        with mock.patch.object(fixtures.X509CertificateAuthenticator, "get_uuid", return_value=TEST_AUTH_UUID):
            with fixtures.create_authenticator(fix, username_attr="CN=([^,]*)") as instance:
                ticket_id = self._create_ticket()
                params = self._make_params(fix.client_pem, ticket_id=ticket_id)
                gm = mock.MagicMock()
                request = mock.MagicMock()
                result = instance.auth_callback(params, gm, request)
                self.assertEqual(result.success, types.auth.AuthenticationState.SUCCESS)
                self.assertEqual(result.username, "john.doe")

    def test_no_username_match(self) -> None:
        fix = fixtures.make_rsa_fixture(client_cn="someone")
        with fixtures.create_authenticator(fix, username_attr="OU=([^,]*)") as instance:
            params = self._make_params(fix.client_pem)
            gm = mock.MagicMock()
            request = mock.MagicMock()
            result = instance.auth_callback(params, gm, request)
            self.assertEqual(result.success, types.auth.AuthenticationState.FAIL)

    def test_get_real_name_from_storage(self) -> None:
        fix = fixtures.make_rsa_fixture(client_cn="realnameuser")
        with mock.patch.object(fixtures.X509CertificateAuthenticator, "get_uuid", return_value=TEST_AUTH_UUID):
            with fixtures.create_authenticator(fix) as instance:
                ticket_id = self._create_ticket()
                params = self._make_params(fix.client_pem, ticket_id=ticket_id)
                gm = mock.MagicMock()
                request = mock.MagicMock()
                result = instance.auth_callback(params, gm, request)
                self.assertEqual(result.success, types.auth.AuthenticationState.SUCCESS)

                # After auth_callback, realname should be stored
                realname = instance.get_real_name(result.username or "")
                self.assertEqual(realname, "realnameuser")

    # --- Replay protection tests ---

    def test_replay_no_ticket_in_payload(self) -> None:
        """Payload without a 'ticket' field must be rejected."""
        fix = fixtures.make_rsa_fixture(client_cn="replay_no_ticket")
        with mock.patch.object(fixtures.X509CertificateAuthenticator, "get_uuid", return_value=TEST_AUTH_UUID):
            with fixtures.create_authenticator(fix) as instance:
                params = self._make_params(fix.client_pem)  # no ticket_id
                gm = mock.MagicMock()
                request = mock.MagicMock()
                result = instance.auth_callback(params, gm, request)
                self.assertEqual(result.success, types.auth.AuthenticationState.FAIL)

    def test_replay_ticket_reused(self) -> None:
        """Same ticket used twice — second call must be rejected."""
        fix = fixtures.make_rsa_fixture(client_cn="replay_reuse")
        with mock.patch.object(fixtures.X509CertificateAuthenticator, "get_uuid", return_value=TEST_AUTH_UUID):
            with fixtures.create_authenticator(fix) as instance:
                ticket_id = self._create_ticket()
                params = self._make_params(fix.client_pem, ticket_id=ticket_id)
                gm = mock.MagicMock()
                request = mock.MagicMock()

                # First call: should succeed
                result1 = instance.auth_callback(params, gm, request)
                self.assertEqual(result1.success, types.auth.AuthenticationState.SUCCESS)
                self.assertEqual(result1.username, "replay_reuse")

                # Second call with same params: ticket already consumed → FAIL
                result2 = instance.auth_callback(params, gm, request)
                self.assertEqual(result2.success, types.auth.AuthenticationState.FAIL)

    def test_replay_ticket_wrong_owner(self) -> None:
        """Ticket owned by a different authenticator must be rejected."""
        fix = fixtures.make_rsa_fixture(client_cn="replay_owner")
        with mock.patch.object(fixtures.X509CertificateAuthenticator, "get_uuid", return_value=TEST_AUTH_UUID):
            with fixtures.create_authenticator(fix) as instance:
                # Create ticket with a different (fake) owner
                fake_owner = "00000000000000000000000000000000"
                ticket_id = TicketStore.create(
                    {"nonce": True},
                    validity=300,
                    owner=fake_owner,
                    secure=True,
                )
                params = self._make_params(fix.client_pem, ticket_id=ticket_id)
                gm = mock.MagicMock()
                request = mock.MagicMock()
                result = instance.auth_callback(params, gm, request)
                self.assertEqual(result.success, types.auth.AuthenticationState.FAIL)

    def test_get_real_name_fallback(self) -> None:
        fix = fixtures.make_rsa_fixture()
        with fixtures.create_authenticator(fix) as instance:
            realname = instance.get_real_name("unknown_user")
            self.assertEqual(realname, "unknown_user")

    def test_get_groups(self) -> None:
        fix = fixtures.make_rsa_fixture()
        with fixtures.create_authenticator(fix, common_groups="group_a,group_b,group_c") as instance:
            gm = mock.MagicMock()
            instance.get_groups("anyuser", gm)
            gm.validate.assert_called_once()
            groups_arg = gm.validate.call_args[0][0]
            self.assertEqual(set(groups_arg), {"group_a", "group_b", "group_c"})


class TestGetJavascript(UDSTestCase):
    """Tests for the get_javascript() method."""

    def test_javascript_redirect_url(self) -> None:
        fix = fixtures.make_rsa_fixture()
        with fixtures.create_authenticator(fix) as instance:
            valid_uuid = "a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5"
            request = mock.MagicMock()
            request.build_absolute_uri.return_value = "https://example.com/uds/cert_auth"
            with mock.patch.object(instance, "get_uuid", return_value=valid_uuid):
                js = instance.get_javascript(request) or ""
                self.assertIn("window.location", js)
                self.assertIn("https://cert-auth.example.com/cert_auth/", js)

    def test_javascript_not_none(self) -> None:
        fix = fixtures.make_rsa_fixture()
        with fixtures.create_authenticator(fix) as instance:
            valid_uuid = "00000000000000000000000000000000"
            request = mock.MagicMock()
            request.build_absolute_uri.return_value = "https://example.com/uds/cert_auth"
            with mock.patch.object(instance, "get_uuid", return_value=valid_uuid):
                js = instance.get_javascript(request)
                self.assertIsNotNone(js)
                self.assertIsInstance(js, str)
