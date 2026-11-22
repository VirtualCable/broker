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

"""Read-only delegations never receive stored credentials on detail routes.

Regression for the Strix pentest_4494 finding: masking used to be
client-opt-in (``$redacted``) on every managed-object serialization path.
The detail routes (``/providers/{provider}/services/...``) serialize the
service module configuration through the same ``ManagedObjectItem``
machinery, so they must enforce the same rule: callers without MANAGEMENT
on the object get sensitive values redacted, with or without ``$redacted``.

The ``TestService`` fixtures declare no password fields, so these tests
pin the permission plumbing (flag presence, non-sensitive values intact,
etag stability); the value-level redaction is covered by
``tests/REST/methods/transports/test_redacted.py``.
"""

import typing

from uds.core import types
from uds.core.util import permissions

from tests.fixtures import services as services_fixtures

from ....utils import rest


class ProviderServicesPermissionRedactionTest(rest.test.RESTTestCase):
    """Detail single-item and listing redaction follows the object permission."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.service = services_fixtures.create_db_service(self.provider)
        self.base = f"providers/{self.provider.uuid}/services"

    def _grant(self, permission: types.permissions.PermissionType) -> None:
        # Detail routes authorize the caller against the parent provider
        # (process_detail), so the delegation is granted there; the object
        # permission drives the redaction decision.
        permissions.add_user_permission(self.staffs[0], self.provider, permission)

    def test_read_grantee_single_item_is_flagged_redacted(self) -> None:
        self._grant(types.permissions.PermissionType.READ)
        self.login(self.staffs[0], as_admin=False)
        response = self.client.rest_get(f"{self.base}/{self.service.uuid}")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIn("_redacted", body)
        # Non-sensitive configuration keeps flowing to the reader
        self.assertEqual(body["name"], self.service.name)

    def test_read_grantee_listing_is_flagged_redacted(self) -> None:
        self._grant(types.permissions.PermissionType.READ)
        self.login(self.staffs[0], as_admin=False)
        for path in (self.base, f"{self.base}/overview"):
            with self.subTest(path=path):
                response = self.client.rest_get(path)
                self.assertEqual(response.status_code, 200, response.content)
                item = next(i for i in response.json() if i["id"] == self.service.uuid)
                self.assertIn("_redacted", item)

    def test_management_grantee_is_not_redacted(self) -> None:
        self._grant(types.permissions.PermissionType.MANAGEMENT)
        self.login(self.staffs[0], as_admin=False)
        response = self.client.rest_get(f"{self.base}/{self.service.uuid}")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn("_redacted", response.json())

    def test_admin_is_not_redacted(self) -> None:
        self.login()
        response = self.client.rest_get(f"{self.base}/{self.service.uuid}")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn("_redacted", response.json())

    def test_redaction_keeps_etag_stable_for_read_grantee(self) -> None:
        self.login()
        admin_view = self.client.rest_get(f"{self.base}/{self.service.uuid}")
        self._grant(types.permissions.PermissionType.READ)
        self.login(self.staffs[0], as_admin=False)
        reader_view = self.client.rest_get(f"{self.base}/{self.service.uuid}")
        self.assertEqual(admin_view.status_code, 200, admin_view.content)
        self.assertEqual(reader_view.status_code, 200, reader_view.content)
        self.assertEqual(admin_view["ETag"], reader_view["ETag"])
