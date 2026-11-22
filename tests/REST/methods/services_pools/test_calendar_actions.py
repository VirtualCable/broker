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
Scheduled actions detail route must be scoped to the parent service pool.

The ``/servicespools/{pool}/actions/{action}`` detail handler authorizes the
caller against the pool in the URL; the item lookup must therefore go through
that pool's relation, never the global table. A staff user granted MANAGEMENT
on pool A and READ on pool B must not be able to delete (or fetch) pool B's
scheduled actions by addressing them through pool A.

Regression for the Strix pentest_4494 finding "Cross-pool deletion of
scheduled calendar actions via the service pool REST detail route".
"""

import typing

from uds import models
from uds.core import types
from uds.core.util import permissions

from tests.fixtures import services as services_fixtures
from tests.utils import rest


class CalendarActionsScopingTest(rest.test.RESTTestCase):
    """Detail route lookups are scoped to the parent service pool."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.service = self.user_service_managed.deployed_service.service
        self.victim_pool = services_fixtures.create_db_servicepool(self.service)
        self.attacker_pool = services_fixtures.create_db_servicepool(self.service)
        self.calendar = models.Calendar.objects.create(name="victim calendar", comments="")
        self.action = models.CalendarAction.objects.create(
            calendar=self.calendar,
            service_pool=self.victim_pool,
            action="PUBLISH",
            at_start=True,
            events_offset=0,
            params="{}",
        )
        # A delegated administrator: MANAGEMENT on their own pool, READ on the victim
        permissions.add_user_permission(
            self.staffs[0], self.attacker_pool, types.permissions.PermissionType.MANAGEMENT
        )
        permissions.add_user_permission(self.staffs[0], self.victim_pool, types.permissions.PermissionType.READ)

    def _delete(self, pool: models.ServicePool, action: models.CalendarAction) -> "typing.Any":
        return self.client.rest_delete(f"servicespools/{pool.uuid}/actions/{action.uuid}")

    def test_staff_cannot_delete_own_read_only_pool_action(self) -> None:
        """READ on the victim pool does not grant deletion through the owning pool."""
        self.login(self.staffs[0], as_admin=False)
        response = self._delete(self.victim_pool, self.action)
        self.assertEqual(response.status_code, 403, response.content)
        self.assertTrue(models.CalendarAction.objects.filter(uuid=self.action.uuid).exists())

    def test_staff_cannot_delete_foreign_action_through_own_pool(self) -> None:
        """The victim action is unknown when addressed through another pool."""
        self.login(self.staffs[0], as_admin=False)
        response = self._delete(self.attacker_pool, self.action)
        self.assertEqual(response.status_code, 404, response.content)
        self.assertTrue(models.CalendarAction.objects.filter(uuid=self.action.uuid).exists())

    def test_admin_deletes_through_owning_pool(self) -> None:
        """The legitimate path keeps working: MANAGEMENT pool + own action."""
        self.login()
        response = self._delete(self.victim_pool, self.action)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(models.CalendarAction.objects.filter(uuid=self.action.uuid).exists())
