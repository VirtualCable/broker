"""
Author: Andres Schumann, aschumann at virtualcable dot es

The e-mail MFA must verify the SMTP server certificate when it logs in with TLS/SSL.
"""

import ssl
import typing
from unittest import mock

from uds.mfas.Email.mfa import EmailMFA

from tests.utils.test import UDSTestCase


class EmailMfaTlsVerificationTest(UDSTestCase):
    def _captured_context(self, security: str) -> ssl.SSLContext:
        mfa = EmailMFA(self.create_environment(), None)
        mfa.hostname.value = "smtp.example.test"
        mfa.security.value = security
        mfa.username.value = ""
        mfa.password.value = ""
        with (
            mock.patch("uds.mfas.Email.mfa.smtplib.SMTP_SSL") as smtp_ssl,
            mock.patch("uds.mfas.Email.mfa.smtplib.SMTP") as smtp,
        ):
            mfa.login()
            client: typing.Any = smtp_ssl if security == "ssl" else smtp
            context = client.call_args.kwargs.get("context")
            if context is None:  # tls: the context goes to starttls()
                context = smtp.return_value.starttls.call_args.kwargs["context"]
        return context

    def test_ssl_verifies_server_certificate(self) -> None:
        context = self._captured_context("ssl")
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_tls_verifies_server_certificate(self) -> None:
        context = self._captured_context("tls")
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
