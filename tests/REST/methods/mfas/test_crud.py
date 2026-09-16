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

import logging
import typing

from uds import models

from ....utils import rest

logger: logging.Logger = logging.getLogger(__name__)

TEST_MFA_TYPE: typing.Final[str] = "emailMFA"
TEST_SUBJECT: typing.Final[str] = "Your verification code"
TEST_HOSTNAME: typing.Final[str] = "smtp.example.com:587"


def _create_payload(name: str) -> dict[str, typing.Any]:
    return {
        "name": name,
        "comments": "created by MFA CRUD test",
        "data_type": TEST_MFA_TYPE,
        "tags": [],
        "remember_device": 0,
        "validity": 5,
        "hostname": TEST_HOSTNAME,
        "security": "tls",
        "username": "",
        "password": "",
        "email_subject": TEST_SUBJECT,
        "from_email": "uds@example.com",
        "enable_html": True,
        "mail_txt": "Your code is {code}",
        "mail_html": "",
    }


class MFAsCrudTest(rest.test.RESTTestCase):
    """The module fields of an MFA must survive a GET and an edit round trip."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def _create(self, name: str) -> dict[str, typing.Any]:
        response = self.client.rest_put("mfa", data=_create_payload(name))
        self.assertEqual(response.status_code, 200, response.content)
        return typing.cast("dict[str, typing.Any]", response.json())

    def _get(self, uuid: str) -> dict[str, typing.Any]:
        response = self.client.rest_get(f"mfa/{uuid}")
        self.assertEqual(response.status_code, 200, response.content)
        return typing.cast("dict[str, typing.Any]", response.json())

    def test_get_item_includes_module_fields(self) -> None:
        """GET must return the module own fields, not only the model columns."""
        item = self._get(self._create("mfa-with-module-fields")["id"])

        self.assertEqual(item["email_subject"], TEST_SUBJECT)
        self.assertEqual(item["hostname"], TEST_HOSTNAME)

    def test_edit_keeps_module_fields(self) -> None:
        """Editing through the payload the admin UI receives must not wipe the module fields."""
        uuid: str = self._create("mfa-before-edit")["id"]

        edit_payload = dict(self._get(uuid))
        edit_payload["name"] = "mfa-after-edit"
        response = self.client.rest_put(f"mfa/{uuid}", data=edit_payload)
        self.assertEqual(response.status_code, 200, response.content)

        item = self._get(uuid)
        self.assertEqual(item["name"], "mfa-after-edit")
        self.assertEqual(item["email_subject"], TEST_SUBJECT)
        self.assertEqual(item["hostname"], TEST_HOSTNAME)

        instance = models.MFA.objects.get(uuid=uuid).get_instance()
        self.assertEqual(instance.email_subject.value, TEST_SUBJECT)  # type: ignore[attr-defined]
        self.assertEqual(instance.hostname.value, TEST_HOSTNAME)  # type: ignore[attr-defined]
