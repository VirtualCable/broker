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
import typing
from unittest import mock

from tests.fixtures import servers as servers_fixtures
from tests.fixtures.modules.osmanager.testing_osmanager import TestOSManager
from tests.utils import rest
from uds import models

DEADLINE: typing.Final[int] = 3600


class LoginDeadlineTest(rest.test.RESTActorTestCase):
    def _actor_v3_login(self) -> typing.Any:
        response = self.client.post(
            "/uds/rest/actor/v3/login",
            data={"token": self.user_service_managed.uuid, "username": "local_user_name"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["result"]["deadline"]

    def _server_event_login(self) -> typing.Any:
        server = servers_fixtures.create_server()
        response = self.client.rest_post(
            "/servers/event",
            data={
                "token": servers_fixtures.raw_token(server),
                "type": "login",
                "userservice_uuid": self.user_service_managed.uuid,
                "username": "local_user_name",
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["result"]["deadline"]

    def _assert_deadline(self, ignore_deadline: bool, expected: int | None) -> None:
        for login in (self._actor_v3_login, self._server_event_login):
            with (
                self.subTest(login=login.__name__),
                mock.patch.object(models.ServicePool, "get_deadline", return_value=DEADLINE),
                mock.patch.object(TestOSManager, "ignore_deadline", return_value=ignore_deadline),
            ):
                self.assertEqual(login(), expected)

    def test_osmanager_honoring_deadline_sends_it(self) -> None:
        self._assert_deadline(ignore_deadline=False, expected=DEADLINE)

    def test_osmanager_ignoring_deadline_sends_none(self) -> None:
        self._assert_deadline(ignore_deadline=True, expected=None)
