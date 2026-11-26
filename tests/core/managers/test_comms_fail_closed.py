#
# Copyright (c) 2026 Virtual Cable S.L.
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
# FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
# OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#

"""
Tests for the comms client failing closed: a user service with a comms url
but no pinned certificate (an anomalous state, only reachable as a partial
commit from older versions) must not be contacted with TLS verification
disabled. Every caller treats NoActorComms gracefully.
"""

import os
from unittest import mock

from tests.fixtures import authenticators as authenticators_fixtures
from tests.fixtures import services as services_fixtures
from tests.utils.test import UDSTestCase
from uds.core import exceptions, types
from uds.core.managers.userservice import UserServiceManager
from uds.core.managers.userservice_helpers import comms
from uds.core.util import security
from uds import models


class CommsFailClosedTest(UDSTestCase):
    def _userservice(self) -> "models.UserService":
        provider = services_fixtures.create_db_provider()
        auth = authenticators_fixtures.create_db_authenticator()
        groups = authenticators_fixtures.create_db_groups(auth)
        users = authenticators_fixtures.create_db_users(auth, number_of_users=1, groups=groups)
        return services_fixtures.create_db_one_assigned_userservice(provider, users[0], groups, "managed")

    def _partial_commit_state(self, userservice: "models.UserService") -> None:
        # Simulate the anomalous "comms url without certificate" state that
        # older versions could persist (URL set, cert generation failed)
        userservice.set_comms_info("https://10.0.0.5:43910/actor/testsecret", "testsecret")
        # The actor must be new enough for the comms client to even consider
        # it (min 3.5.0), otherwise OldActorVersion is raised first
        userservice.properties["actor_version"] = "4.1.0"
        # Ensure no cert is stored (the anomalous state)
        if userservice.properties.get("cert"):
            del userservice.properties["cert"]

    def test_execute_request_fails_closed_without_cert(self) -> None:
        userservice = self._userservice()
        self._partial_commit_state(userservice)
        with self.assertRaises(exceptions.actor.NoActorComms):
            comms._execute_actor_request(userservice, "uuid")

    def test_no_session_is_created_without_cert(self) -> None:
        # The request must never be performed with TLS verification disabled:
        # no session (even a verifying one) is built at all
        userservice = self._userservice()
        self._partial_commit_state(userservice)
        with (
            mock.patch.object(security, "secure_requests_session") as session_factory,
            self.assertRaises(exceptions.actor.NoActorComms),
        ):
            comms._execute_actor_request(userservice, "uuid")
        session_factory.assert_not_called()

    def test_notify_preconnect_handles_fail_closed(self) -> None:
        # The caller chain treats NoActorComms gracefully (service fallback)
        userservice = self._userservice()
        self._partial_commit_state(userservice)
        with mock.patch.object(security, "secure_requests_session") as session_factory:
            # Must not raise
            UserServiceManager.manager().notify_preconnect(
                userservice,
                types.connections.ConnectionData(
                    username="user0", protocol="rdp", service_type=types.services.ServiceType.VDI
                ),
            )
        session_factory.assert_not_called()

    def test_other_callers_handle_fail_closed(self) -> None:
        userservice = self._userservice()
        self._partial_commit_state(userservice)
        with mock.patch.object(security, "secure_requests_session") as session_factory:
            UserServiceManager.manager().request_screenshot(userservice)
            UserServiceManager.manager().send_script(userservice, "print('x')")
            UserServiceManager.manager().request_logoff(userservice)
            UserServiceManager.manager().send_message(userservice, "message")
        session_factory.assert_not_called()

    def _cert_state(self, userservice: "models.UserService") -> None:
        userservice.set_comms_info("https://10.0.0.5:43910/actor/testsecret", "testsecret")
        userservice.properties["actor_version"] = "4.1.0"
        userservice.properties["cert"] = "-----BEGIN CERTIFICATE-----\ntestcert\n-----END CERTIFICATE-----"

    def _run_capturing_verify(self, userservice: "models.UserService", session: mock.Mock) -> str:
        # Run a GET request with the mocked session and capture the temp
        # certificate file path handed to requests as "verify"
        with mock.patch.object(comms, "secure_requests_session", return_value=session):
            self.assertEqual(comms._execute_actor_request(userservice, "uuid"), "ok")
        verify = session.get.call_args.kwargs["verify"]
        self.assertIsInstance(verify, str)
        return verify

    def test_verify_tempfile_removed_on_success(self) -> None:
        userservice = self._userservice()
        self._cert_state(userservice)
        session = mock.Mock()
        session.get.return_value.json.return_value = {"result": "ok"}
        verify = self._run_capturing_verify(userservice, session)
        self.assertFalse(os.path.exists(verify))

    def test_verify_tempfile_removed_on_request_failure(self) -> None:
        # Even when the request fails (exception path), the temp certificate
        # file must not be left behind
        userservice = self._userservice()
        self._cert_state(userservice)
        session = mock.Mock()
        session.get.side_effect = OSError("connection refused")
        with mock.patch.object(comms, "secure_requests_session", return_value=session):
            self.assertIsNone(comms._execute_actor_request(userservice, "uuid"))
        verify = session.get.call_args.kwargs["verify"]
        self.assertFalse(os.path.exists(verify))

    def test_verify_tempfile_removed_on_invalid_json(self) -> None:
        # A failure after the request succeeded (bad json) must also clean up
        userservice = self._userservice()
        self._cert_state(userservice)
        session = mock.Mock()
        session.get.return_value.json.side_effect = ValueError("not json")
        with mock.patch.object(comms, "secure_requests_session", return_value=session):
            self.assertIsNone(comms._execute_actor_request(userservice, "uuid"))
        verify = session.get.call_args.kwargs["verify"]
        self.assertFalse(os.path.exists(verify))
