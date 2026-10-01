#
# Copyright (c) 2026 Virtual Cable S.L.U.
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
#    * Neither the name of Virtual Cable S.L.U. nor the names of its contributors
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
"""

from uds import models
from uds.core.util import fields

from tests.fixtures import services as services_fixtures

from ...utils.test import UDSTransactionTestCase
from . import fixtures

TOTAL_MACHINES = len(fixtures.SERVER_GROUP_IPS_MACS)


class TestPoolUsageWithMaintenance(UDSTransactionTestCase):
    def _pool_with_machines_in_maintenance(self, in_maintenance: int) -> models.ServicePool:
        service = fixtures.create_service_multi()
        servers = fields.get_server_group_from_field(service.server_group).servers.all()
        for server in servers[:in_maintenance]:
            server.maintenance_mode = True
            server.save(update_fields=["maintenance_mode"])
        return services_fixtures.create_db_servicepool(service.db_obj())

    def test_machines_in_maintenance_count(self) -> None:
        pool = self._pool_with_machines_in_maintenance(2)
        self.assertEqual(pool.service.get_instance().machines_in_maintenance(), 2)

    def test_total_without_maintenance(self) -> None:
        usage = self._pool_with_machines_in_maintenance(0).usage()
        self.assertEqual(usage.total, TOTAL_MACHINES)

    def test_total_discounts_machines_in_maintenance(self) -> None:
        usage = self._pool_with_machines_in_maintenance(2).usage()
        self.assertEqual(usage.total, TOTAL_MACHINES - 2)

    def test_total_is_zero_when_every_machine_is_in_maintenance(self) -> None:
        usage = self._pool_with_machines_in_maintenance(TOTAL_MACHINES).usage()
        self.assertEqual(usage.total, 0)

    def test_total_never_drops_below_machines_in_use(self) -> None:
        pool = self._pool_with_machines_in_maintenance(TOTAL_MACHINES)
        usage = pool.usage(cached_value=2)
        self.assertEqual((usage.used, usage.total), (2, 2))

    def test_limit_of_user_services_still_counts_machines_in_maintenance(self) -> None:
        pool = self._pool_with_machines_in_maintenance(2)
        self.assertEqual(pool.get_max(), TOTAL_MACHINES)

    def test_metapool_total_adds_discounted_pools(self) -> None:
        first = self._pool_with_machines_in_maintenance(2)
        second = services_fixtures.create_db_servicepool(first.service)
        meta = services_fixtures.create_db_metapool([first, second], [])
        self.assertEqual(meta.usage().total, 2 * (TOTAL_MACHINES - 2))
