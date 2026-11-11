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
Author: Andres Schumann, aschumann at virtualcable dot es

The e-mail notifier must verify the SMTP server certificate when it logs in with TLS/SSL.
"""

import ssl
import typing
from unittest import mock

from uds.notifiers.email.notifier import EmailNotifier

from tests.utils.test import UDSTestCase


class EmailTlsVerificationTest(UDSTestCase):
    def _notifier(self, security: str) -> EmailNotifier:
        notifier = EmailNotifier(environment=self.create_environment(), values=None)
        notifier.hostname.value = "smtp.example.test"
        notifier.security.value = security
        notifier.username.value = ""
        notifier.password.value = ""
        return notifier

    def _captured_context(self, security: str) -> ssl.SSLContext:
        notifier = self._notifier(security)
        with mock.patch("uds.notifiers.email.notifier.smtplib.SMTP_SSL") as smtp_ssl, mock.patch(
            "uds.notifiers.email.notifier.smtplib.SMTP"
        ) as smtp:
            notifier.login()
            client: typing.Any = smtp_ssl if security == "ssl" else smtp
            context = client.call_args.kwargs.get("context")
            if context is None:  # tls: context goes to starttls()
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
