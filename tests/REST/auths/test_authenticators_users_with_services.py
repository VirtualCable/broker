# -*- coding: utf-8 -*-
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
Author: Adolfo Gómez, dkmaster at dkmon dot com
"""
import datetime

from django.utils import timezone

from uds.core.types.states import State

from ...utils import rest


class AuthenticatorUsersWithServicesTest(rest.test.RESTTestCase):
    """
    Test the "users_with_services" custom method of the authenticators handler
    """

    def setUp(self) -> None:
        timezone.activate(datetime.timezone.utc)
        super().setUp()
        self.login()

    def url(self) -> str:
        return f'authenticators/{self.auth.uuid}/users_with_services'

    def test_lists_the_users_owning_a_service(self) -> None:
        response = self.client.rest_get(self.url())
        self.assertEqual(response.status_code, 200)

        # The test case gives every user a couple of USABLE userservices
        self.assertEqual({i['name'] for i in response.json()}, {i.name for i in self.users})

    def test_a_user_owning_several_services_appears_once(self) -> None:
        users = self.client.rest_get(self.url()).json()
        names = [i['name'] for i in users]

        self.assertEqual(len(names), len(set(names)))

    def test_skips_users_without_a_valid_service(self) -> None:
        orphan = self.plain_users[0]
        orphan.userServices.update(state=State.REMOVED)

        users = self.client.rest_get(self.url()).json()

        self.assertNotIn(orphan.name, {i['name'] for i in users})
        self.assertEqual(len(users), len(self.users) - 1)
