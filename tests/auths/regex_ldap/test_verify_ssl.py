"""
RegexLdap must use its verify_ssl and certificate settings when it connects over LDAPS.
"""

import typing
from unittest import mock

from uds.auths.RegexLdap.authenticator import RegexLdap

from tests.utils.test import UDSTestCase

CONNECTION: typing.Final = "uds.auths.RegexLdap.authenticator.ldaputil.connection"


class RegexLdapVerifySslTest(UDSTestCase):
    def _authenticator(self, verify_ssl: bool) -> RegexLdap:
        auth = RegexLdap(self.create_environment(), None)
        auth.use_ssl.value = True
        auth.verify_ssl.value = verify_ssl
        auth.certificate.value = "DUMMY-CA"
        auth.host.value = "dc.example.test"
        auth.port.value = 636
        return auth

    def test_bind_connection_honours_verify_ssl(self) -> None:
        auth = self._authenticator(verify_ssl=True)
        with mock.patch(CONNECTION) as connection:
            auth._stablish_connection()
        self.assertTrue(connection.call_args.kwargs["verify_ssl"])
        self.assertEqual(connection.call_args.kwargs["certificate_data"], "DUMMY-CA")

    def test_bind_connection_without_verification_when_disabled(self) -> None:
        auth = self._authenticator(verify_ssl=False)
        with mock.patch(CONNECTION) as connection:
            auth._stablish_connection()
        self.assertFalse(connection.call_args.kwargs["verify_ssl"])

    def test_saved_v5_record_keeps_verify_ssl_and_certificate(self) -> None:
        auth = RegexLdap(self.create_environment(), None)
        record = (
            "v5\tdc.example.test\t636\tTrue\tsvc\tDUMMY-PASSWORD\t10\tDC=example,DC=test"
            "\tperson\tsAMAccountName\tmemberOf\tcn\t\t\tTrue\tDUMMY-CA"
        )
        auth.unmarshal(record.encode("utf8"))
        self.assertTrue(auth.verify_ssl.as_bool())
        self.assertEqual(auth.certificate.as_str(), "DUMMY-CA")

    def test_user_connection_honours_verify_ssl(self) -> None:
        auth = self._authenticator(verify_ssl=True)
        with mock.patch(CONNECTION) as connection:
            auth._stablish_connection_as("user@example.test", "DUMMY-PASSWORD")
        self.assertTrue(connection.call_args.kwargs["verify_ssl"])
        self.assertEqual(connection.call_args.kwargs["certificate_data"], "DUMMY-CA")
