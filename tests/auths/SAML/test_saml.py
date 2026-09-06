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
Contracts for SAMLAuthenticator.get_info html rendering.

When the SP metadata is requested as ``format=html``, the (XML) metadata is
rendered inside an HTML page. Every single character that is significant for
HTML must be escaped, not only ``<`` as it was done previously.
"""

import html
import typing
from unittest import mock

from tests.utils.test import UDSTestCase
from uds.auths.SAML.saml import SAMLAuthenticator
from uds.core.environment import Environment

METADATA: typing.Final[str] = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" entityID="https://sp.example.net/metadata">\n'
    '  <md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true">\n'
    "  </md:SPSSODescriptor>\n"
    "</md:EntityDescriptor>\n"
)

XSS_METADATA: typing.Final[str] = (
    '<EntityDescriptor entityID="&amp;evil"><script>alert("xss")</script></EntityDescriptor>\n'
)


class SAMLGetInfoTest(UDSTestCase):
    def test_html_escaping_escapes_all_html_significant_chars(self) -> None:
        with Environment.temporary_environment() as env:
            instance = SAMLAuthenticator(environment=env)
            with mock.patch.object(instance, "get_sp_metadata", return_value=METADATA):
                info, content_type = typing.cast("tuple[str, str | None]", instance.get_info({"format": "html"}))

        self.assertEqual(content_type, "text/html")
        # Every line must be fully html-escaped and joined with <br/>
        raw_lines = METADATA.splitlines()
        escaped_lines = info.split("<br/>")
        self.assertEqual(len(escaped_lines), len(raw_lines))
        for escaped, raw in zip(escaped_lines, raw_lines):
            with self.subTest(raw=raw):
                self.assertEqual(escaped, html.escape(raw))
        # And no raw metadata markup survives the escaping
        for line in escaped_lines:
            with self.subTest(line=line):
                self.assertNotIn("<", line)
                self.assertNotIn(">", line)
                self.assertNotIn('"', line)

    def test_html_escaping_neutralizes_injected_markup(self) -> None:
        with Environment.temporary_environment() as env:
            instance = SAMLAuthenticator(environment=env)
            with mock.patch.object(instance, "get_sp_metadata", return_value=XSS_METADATA):
                info, _content_type = typing.cast("tuple[str, str | None]", instance.get_info({"format": "html"}))

        self.assertNotIn("<script>", info)
        self.assertIn("&lt;script&gt;", info)
        self.assertIn("&amp;amp;", info)  # Original &amp; is double escaped
        self.assertIn("&quot;xss&quot;", info)

    def test_default_format_returns_raw_metadata(self) -> None:
        with Environment.temporary_environment() as env:
            instance = SAMLAuthenticator(environment=env)
            with mock.patch.object(instance, "get_sp_metadata", return_value=METADATA):
                info, content_type = typing.cast("tuple[str, str | None]", instance.get_info({}))

        self.assertEqual(info, METADATA)
        self.assertEqual(content_type, "application/samlmetadata+xml")
