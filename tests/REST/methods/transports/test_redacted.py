#
# Copyright (c) 2012-2023 Virtual Cable S.L.
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
Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import typing

from uds import models
from uds.core import types
from uds.core.consts.rest import REDACTED
from uds.core.util import permissions

from tests.fixtures import services as services_fixtures

from ....utils import rest

# Test transport type available in tests/fixtures/modules/transport/transport.py
# Its ``pin`` field is a PasswordField with a name outside the global redaction
# denylist, so it is the perfect probe for ``$redacted``.
TEST_TRANSPORT_TYPE: typing.Final[str] = "TestTransport"


def _base_payload(name: str) -> dict[str, typing.Any]:
    return {
        "name": name,
        "comments": "redaction smoke test",
        "data_type": TEST_TRANSPORT_TYPE,
        "tags": [],
        "priority": 1,
        "net_filtering": "D",
        "allowed_oss": "",
        "label": "",
        "test_url": "https://example.com",
        "pin": "1234",
    }


class TransportsRedactedTest(rest.test.RESTTestCase):
    """``$redacted`` on /transports: redact at origin, flag, and guards."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        response = self.client.rest_put("transports", data=_base_payload("redacted-transport"))
        self.assertEqual(response.status_code, 200, response.content)
        self.uuid: str = response.json()["id"]

    def test_overview_redacted(self) -> None:
        """GET ?$redacted replaces sensitive values and adds the flag."""
        response = self.client.rest_get("transports/overview?$redacted")
        self.assertEqual(response.status_code, 200)
        items: list[dict[str, typing.Any]] = response.json()
        item = next(i for i in items if i["id"] == self.uuid)
        self.assertEqual(item["instance"]["pin"], REDACTED)
        self.assertEqual(item["pin"], REDACTED)  # Legacy flattened field
        self.assertEqual(item["instance"]["test_url"], "https://example.com")
        self.assertTrue(item["_redacted"])

    def test_overview_not_redacted_by_default(self) -> None:
        """Without ``$redacted``, values are real and no flag is present."""
        response = self.client.rest_get("transports/overview")
        self.assertEqual(response.status_code, 200)
        items: list[dict[str, typing.Any]] = response.json()
        item = next(i for i in items if i["id"] == self.uuid)
        self.assertEqual(item["instance"]["pin"], "1234")
        self.assertNotIn("_redacted", item)

    def test_detail_redacted_keeps_etag_stable(self) -> None:
        """Single item GET: redacted values, but identical ETag."""
        plain = self.client.rest_get(f"transports/{self.uuid}")
        redacted = self.client.rest_get(f"transports/{self.uuid}?$redacted")
        self.assertEqual(plain.status_code, 200, plain.content)
        self.assertEqual(redacted.status_code, 200, redacted.content)
        self.assertEqual(plain["ETag"], redacted["ETag"])
        self.assertEqual(redacted.json()["instance"]["pin"], REDACTED)
        self.assertTrue(redacted.json()["_redacted"])
        self.assertEqual(plain.json()["instance"]["pin"], "1234")
        self.assertNotIn("_redacted", plain.json())

    def test_write_with_redacted_param_is_refused(self) -> None:
        """``$redacted`` on a write request is rejected."""
        response = self.client.rest_put(f"transports/{self.uuid}?$redacted", data=_base_payload("try-write"))
        self.assertEqual(response.status_code, 400, response.content)

    def test_write_with_redacted_flag_in_body_is_refused(self) -> None:
        """Echoing a redacted item (body carries ``_redacted``) is rejected."""
        payload = _base_payload("try-flag-write")
        payload["_redacted"] = True
        response = self.client.rest_put(f"transports/{self.uuid}", data=payload)
        self.assertEqual(response.status_code, 400, response.content)


class TransportsPermissionRedactionTest(rest.test.RESTTestCase):
    """Read-only delegations never receive stored credentials.

    Regression for the Strix pentest_4494 finding: masking used to be
    client-opt-in (``$redacted``), so a staff user holding only READ on a
    transport got its secrets in clear text. Redaction is now mandatory
    for callers without MANAGEMENT on the object; MANAGEMENT+ grantees
    and administrators keep the previous behavior.
    """

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        response = self.client.rest_put("transports", data=_base_payload("perm-redaction-transport"))
        self.assertEqual(response.status_code, 200, response.content)
        self.uuid: str = response.json()["id"]
        self.transport = models.Transport.objects.get(uuid=self.uuid)

    def _grant(self, permission: types.permissions.PermissionType) -> None:
        permissions.add_user_permission(self.staffs[0], self.transport, permission)

    def test_read_grantee_gets_redacted_single_item_without_asking(self) -> None:
        self._grant(types.permissions.PermissionType.READ)
        self.login(self.staffs[0], as_admin=False)
        response = self.client.rest_get(f"transports/{self.uuid}")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["instance"]["pin"], REDACTED)
        self.assertEqual(body["pin"], REDACTED)  # Legacy flattened field
        self.assertEqual(body["instance"]["test_url"], "https://example.com")
        self.assertTrue(body["_redacted"])

    def test_read_grantee_gets_redacted_listings_without_asking(self) -> None:
        self._grant(types.permissions.PermissionType.READ)
        self.login(self.staffs[0], as_admin=False)
        for path in (f"transports/{self.uuid}", "transports/overview", "transports"):
            with self.subTest(path=path):
                response = self.client.rest_get(path)
                self.assertEqual(response.status_code, 200, response.content)
                body = typing.cast("list[dict[str, typing.Any]] | dict[str, typing.Any]", response.json())
                items = body if isinstance(body, list) else [body]
                item = next(i for i in items if i["id"] == self.uuid)
                self.assertEqual(item["instance"]["pin"], REDACTED)
                self.assertTrue(item["_redacted"])

    def test_management_grantee_keeps_real_values(self) -> None:
        self._grant(types.permissions.PermissionType.MANAGEMENT)
        self.login(self.staffs[0], as_admin=False)
        response = self.client.rest_get(f"transports/{self.uuid}")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["instance"]["pin"], "1234")
        self.assertNotIn("_redacted", body)

    def test_management_grantee_can_still_opt_in(self) -> None:
        self._grant(types.permissions.PermissionType.MANAGEMENT)
        self.login(self.staffs[0], as_admin=False)
        response = self.client.rest_get(f"transports/{self.uuid}?$redacted")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["instance"]["pin"], REDACTED)

    def test_admin_keeps_real_values(self) -> None:
        response = self.client.rest_get(f"transports/{self.uuid}")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["instance"]["pin"], "1234")

    def test_redaction_keeps_etag_stable_for_read_grantee(self) -> None:
        """The mandatory redaction must not change concurrency tokens."""
        admin_view = self.client.rest_get(f"transports/{self.uuid}")
        self._grant(types.permissions.PermissionType.READ)
        self.login(self.staffs[0], as_admin=False)
        reader_view = self.client.rest_get(f"transports/{self.uuid}")
        self.assertEqual(admin_view.status_code, 200, admin_view.content)
        self.assertEqual(reader_view.status_code, 200, reader_view.content)
        self.assertEqual(admin_view["ETag"], reader_view["ETag"])

    def test_listings_redact_per_object_permission(self) -> None:
        """A mixed listing redacts only the objects the caller cannot manage."""
        other = services_fixtures.create_db_transport()
        self._grant(types.permissions.PermissionType.MANAGEMENT)
        permissions.add_user_permission(self.staffs[0], other, types.permissions.PermissionType.READ)
        self.login(self.staffs[0], as_admin=False)
        response = self.client.rest_get("transports/overview")
        self.assertEqual(response.status_code, 200, response.content)
        items: list[dict[str, typing.Any]] = response.json()
        managed = next(i for i in items if i["id"] == self.uuid)
        read_only = next(i for i in items if i["id"] == other.uuid)
        self.assertNotIn("_redacted", managed)
        self.assertTrue(read_only["_redacted"])
