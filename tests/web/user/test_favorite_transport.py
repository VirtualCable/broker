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
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""
from django.urls import reverse

from tests.fixtures import services as fixtures_services
from uds import models
from uds.core.util.config import GlobalConfig

from ...utils.web import test


class FavoriteTransportTest(test.WEBTestCase):
    user: models.User
    pool: models.ServicePool
    transports: list[models.Transport]

    def setUp(self) -> None:
        super().setUp()
        GlobalConfig.ENABLE_FAVORITE_TRANSPORTS.set(True)
        self.user = self.login(as_admin=False)
        self.pool = fixtures_services.create_db_assigned_userservices(
            user=self.user, groups=self.user.groups.all()
        )[0].deployed_service
        self.transports = [fixtures_services.create_db_transport(priority=i) for i in range(3)]
        self.pool.transports.set(self.transports[:2])

    def _favorite(self, service_id: str, transport_id: str) -> int:
        url = reverse('webapi.favorite_transport', kwargs={'service_id': service_id, 'transport_id': transport_id})
        return self.client.post(url).status_code

    def _unfavorite(self, service_id: str) -> int:
        url = reverse('webapi.action', kwargs={'service_id': service_id, 'action_string': 'unfavorite-transport'})
        return self.client.post(url).status_code

    def _stored(self) -> dict[str, str]:
        self.user.refresh_from_db()
        return self.user.get_favorite_transports()

    def test_offered_transport_is_stored(self) -> None:
        self.assertEqual(self._favorite('F' + self.pool.uuid, self.transports[1].uuid), 200)

        self.assertEqual(self._stored(), {self.pool.uuid: self.transports[1].uuid})

    def test_unfavorite_clears_it(self) -> None:
        self._favorite('F' + self.pool.uuid, self.transports[1].uuid)

        self.assertEqual(self._unfavorite('F' + self.pool.uuid), 200)
        self.assertEqual(self._stored(), {})

    def test_rejected_when_feature_disabled(self) -> None:
        GlobalConfig.ENABLE_FAVORITE_TRANSPORTS.set(False)

        self.assertEqual(self._favorite('F' + self.pool.uuid, self.transports[1].uuid), 400)
        self.assertEqual(self._stored(), {})

    def test_rejected_for_transport_not_in_pool(self) -> None:
        self.assertEqual(self._favorite('F' + self.pool.uuid, self.transports[2].uuid), 400)
        self.assertEqual(self._stored(), {})

    def test_rejected_for_unknown_service(self) -> None:
        self.assertEqual(self._favorite('F' + self.transports[0].uuid, self.transports[0].uuid), 400)
        self.assertEqual(self._stored(), {})

    def test_rejected_for_pool_of_other_users(self) -> None:
        self.pool.assignedGroups.clear()

        self.assertEqual(self._favorite('F' + self.pool.uuid, self.transports[1].uuid), 400)
        self.assertEqual(self._stored(), {})

    def test_rejected_when_pool_hides_transports(self) -> None:
        self.pool.show_transports = False
        self.pool.save()

        self.assertEqual(self._favorite('F' + self.pool.uuid, self.transports[1].uuid), 400)
        self.assertEqual(self._stored(), {})
