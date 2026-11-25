#
# Copyright (c) 2022 Virtual Cable S.L.
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
Author: Andres Schumann, aschumann at virtualcable dot es
"""

import logging
import typing
from datetime import UTC, datetime

from uds import models
from uds.core import consts

from ....fixtures import servers as servers_fixtures
from ....utils import rest

logger: logging.Logger = logging.getLogger(__name__)


class ServerEventsLoginLogoutTest(rest.test.RESTTestCase):
    """
    Test server functionality
    """

    server: "models.Server"

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.server = servers_fixtures.create_server()

    def test_login(self) -> None:
        # REST path: servers/notify  (/uds/rest/...)
        # loginData = {
        #     'token': 'server token', # Must be present on all events
        #     'type': 'login',  # MUST BE PRESENT
        #     'userservice_uuid': 'uuid', # MUST BE PRESENT
        #     'username': 'username', # Optional
        # }
        # Returns:
        #
        # {
        #     'ip': src.ip,
        #     'hostname': src.hostname,
        #     'deadline': deadLine,
        #     'max_idle': maxIdle,
        #     'session_id': session_id,
        # }
        response = self.client.rest_post(
            "/servers/event",
            data={
                "token": servers_fixtures.raw_token(self.server),
                "type": "login",
                "userservice_uuid": self.user_service_managed.uuid,
                "username": "local_user_name",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.user_service_managed.refresh_from_db()
        self.assertEqual(self.user_service_managed.in_use, True)
        result = response.json()["result"]
        self.assertEqual(self.user_service_managed.src_ip, result["ip"])
        self.assertEqual(self.user_service_managed.src_hostname, result["hostname"])
        session = self.user_service_managed.sessions.first()
        if session is None:
            self.fail("Session not found")
        self.assertEqual(session.session_id, result["session_id"])
        self.assertEqual(self.user_service_managed.properties.get("last_username", ""), "local_user_name")
        # Must not have ticket
        self.assertIsNone(result.get("ticket", None))

    def test_failed_event_does_not_log_the_server_token(self) -> None:
        """A failing event processor must not write the bearer token in clear text.

        Regression for the Strix pentest_4494 finding: the exception handler
        logged the whole event body, which still carries the server token,
        while the REST boundary masks it with ``sanitize_params``.
        """
        raw = servers_fixtures.raw_token(self.server)
        with self.assertLogs("uds.core.managers.servers_api.events", level="ERROR") as logs:
            response = self.client.rest_post(
                "/servers/event",
                data={
                    "token": raw,
                    "type": "login",
                    "userservice_uuid": "nonexistent-uuid",
                    "username": "local_user_name",
                },
            )
        self.assertEqual(response.status_code, 200)
        # The failure is reported in the body, not through the http status
        self.assertEqual(response.json()["result"], "error")
        output = "\n".join(logs.output)
        self.assertNotIn(raw, output)
        self.assertIn("********", output)

    def test_login_with_ticket(self) -> None:
        ticket_uuid = models.TicketStore.create(
            {"userservice_uuid": self.user_service_managed.uuid, "some_value": "value"}
        )
        response = self.client.rest_post(
            "/servers/event",
            data={
                "token": servers_fixtures.raw_token(self.server),
                "type": "login",
                "username": "local_user_name",
                "ticket": ticket_uuid,
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()["result"]
        self.assertEqual(data["ticket"]["some_value"], "value")

    def test_login_with_foreign_ticket_is_refused(self) -> None:
        """A ticket belongs to exactly one user service: presenting one to
        operate on a different service is refused (transitional hardening of
        the server event scope, see the actor credential phase-out plan).
        Legitimate flows always agree, because the ticket is created for the
        service being logged in."""
        other_service = self.userservice_unmanaged
        ticket_uuid = models.TicketStore.create(
            {"userservice_uuid": self.user_service_managed.uuid, "some_value": "value"}
        )
        response = self.client.rest_post(
            "/servers/event",
            data={
                "token": servers_fixtures.raw_token(self.server),
                "type": "login",
                "username": "local_user_name",
                "userservice_uuid": other_service.uuid,
                "ticket": ticket_uuid,
            },
        )
        self.assertEqual(response.status_code, 200)
        # The failure is reported in the body: no login happened on the
        # foreign service
        self.assertEqual(response.json()["result"], "error")
        self.assertIn("Ticket does not belong", response.json()["error"])
        other_service.refresh_from_db()
        self.assertFalse(other_service.in_use)

    def test_login_fail(self) -> None:
        response = self.client.rest_post(
            "/servers/event",
            data={
                "token": servers_fixtures.raw_token(self.server),
                "type": "login",
                "userservice_uuid": "invalid uuid",
                "username": "local_user_name",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.content)
        self.assertIsNotNone(response.json())
        self.assertIn("error", response.json())

    def test_logout(self) -> None:
        # REST path: servers/notify  (/uds/rest/...)
        # logoutData = {
        #     'token': 'server token', # Must be present on all events
        #     'type': 'login',  # MUST BE PRESENT
        #     'userservice_uuid': 'uuid', # MUST BE PRESENT
        #     'username': 'username', # Optional
        # }
        response = self.client.rest_post(
            "/servers/event",
            data={
                "token": servers_fixtures.raw_token(self.server),
                "type": "logout",
                "userservice_uuid": self.user_service_managed.uuid,
                "username": "local_user_name",
                "session_id": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.user_service_managed.in_use, False)

    def test_logout_closes_the_session_named_by_the_server(self) -> None:
        session_id = self.user_service_managed.start_session()
        response = self.client.rest_post(
            "/servers/event",
            data={
                "token": servers_fixtures.raw_token(self.server),
                "type": "logout",
                "userservice_uuid": self.user_service_managed.uuid,
                "username": "local_user_name",
                "session_id": session_id,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], consts.OK)
        session = self.user_service_managed.sessions.get(session_id=session_id)
        self.assertIsNotNone(session.end)

    def test_logout_keeps_other_sessions_of_the_same_user_service_open(self) -> None:
        closing_session_id = self.user_service_managed.start_session()
        open_session_id = self.user_service_managed.start_session()
        response = self.client.rest_post(
            "/servers/event",
            data={
                "token": servers_fixtures.raw_token(self.server),
                "type": "logout",
                "userservice_uuid": self.user_service_managed.uuid,
                "username": "local_user_name",
                "session_id": closing_session_id,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], consts.OK)
        self.assertIsNotNone(self.user_service_managed.sessions.get(session_id=closing_session_id).end)
        self.assertIsNone(self.user_service_managed.sessions.get(session_id=open_session_id).end)

    def test_logout_keeps_the_end_time_of_a_session_already_closed(self) -> None:
        session_id = self.user_service_managed.start_session()
        session = self.user_service_managed.sessions.get(session_id=session_id)
        first_end = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
        session.end = first_end
        session.save(update_fields=["end"])
        response = self.client.rest_post(
            "/servers/event",
            data={
                "token": servers_fixtures.raw_token(self.server),
                "type": "logout",
                "userservice_uuid": self.user_service_managed.uuid,
                "username": "local_user_name",
                "session_id": session_id,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], consts.OK)
        session.refresh_from_db()
        self.assertEqual(session.end, first_end)

    def test_closing_from_a_stale_instance_keeps_the_first_end_time(self) -> None:
        session_id = self.user_service_managed.start_session()
        stale = self.user_service_managed.sessions.get(session_id=session_id)
        first_end = datetime(2026, 11, 24, 10, 0, tzinfo=UTC)
        self.user_service_managed.sessions.filter(session_id=session_id).update(end=first_end)
        stale.close()
        self.assertEqual(stale.end, first_end)
        self.assertEqual(self.user_service_managed.sessions.get(session_id=session_id).end, first_end)

    def test_logout_with_empty_session_id_keeps_open_sessions(self) -> None:
        session_id = self.user_service_managed.start_session()
        response = self.client.rest_post(
            "/servers/event",
            data={
                "token": servers_fixtures.raw_token(self.server),
                "type": "logout",
                "userservice_uuid": self.user_service_managed.uuid,
                "username": "local_user_name",
                "session_id": "",
            },
        )
        self.assertEqual(response.json()["result"], consts.OK)
        self.assertIsNone(self.user_service_managed.sessions.get(session_id=session_id).end)

    def test_logout_without_username_is_refused_and_keeps_sessions_open(self) -> None:
        session_id = self.user_service_managed.start_session()
        response = self.client.rest_post(
            "/servers/event",
            data={
                "token": servers_fixtures.raw_token(self.server),
                "type": "logout",
                "userservice_uuid": self.user_service_managed.uuid,
                "session_id": session_id,
            },
        )
        self.assertIn("error", response.json())
        self.assertIsNone(self.user_service_managed.sessions.get(session_id=session_id).end)

    def test_logout_without_session_id_is_refused_and_keeps_sessions_open(self) -> None:
        session_id = self.user_service_managed.start_session()
        response = self.client.rest_post(
            "/servers/event",
            data={
                "token": servers_fixtures.raw_token(self.server),
                "type": "logout",
                "userservice_uuid": self.user_service_managed.uuid,
                "username": "local_user_name",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("error", response.json())
        self.assertIsNone(self.user_service_managed.sessions.get(session_id=session_id).end)

    def test_logout_fail(self) -> None:
        response = self.client.rest_post(
            "/servers/event",
            data={
                "token": servers_fixtures.raw_token(self.server),
                "type": "login",
                "userservice_uuid": "invalid uuid",
                "username": "local_user_name",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.content)
        self.assertIsNotNone(response.json())
        self.assertIn("error", response.json())

        # No session id, shouls return error
        response = self.client.rest_post(
            "/servers/event",
            data={
                "token": servers_fixtures.raw_token(self.server),
                "type": "logout",
                "user_service": self.user_service_managed.uuid,
                "username": "local_user_name",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.content)
        self.assertIsNotNone(response.json())
        self.assertIn("error", response.json())

    def test_loging_logout(self) -> None:
        response = self.client.rest_post(
            "/servers/event",
            data={
                "token": servers_fixtures.raw_token(self.server),
                "type": "login",
                "userservice_uuid": self.user_service_managed.uuid,
                "username": "local_user_name",
            },
        )
        self.assertEqual(response.status_code, 200)
        session_id = response.json()["result"]["session_id"]
        self.assertIsNotNone(session_id)
        self.user_service_managed.refresh_from_db()
        self.assertEqual(self.user_service_managed.in_use, True)

        response = self.client.rest_post(
            "/servers/event",
            data={
                "token": servers_fixtures.raw_token(self.server),
                "type": "logout",
                "userservice_uuid": self.user_service_managed.uuid,
                "username": "local_user_name",
                "session_id": session_id,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.user_service_managed.refresh_from_db()
        self.assertEqual(self.user_service_managed.in_use, False)
