#
# Copyright (c) 2024 Virtual Cable S.L.
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

import logging
import typing

from unittest import mock

from tests.utils import rest
from uds.core import consts
from uds.core import types
from uds.core.util.model import sql_stamp_seconds
from tests.fixtures.modules.osmanager.testing_osmanager import TestOSManager
from uds import models

logger: logging.Logger = logging.getLogger(__name__)


class ActorReadyTest(rest.test.RESTActorTestCase):
    """
    Test actor functionality
    """

    def test_ready(self) -> None:
        """
        Test actor initialize v3 for managed actor
        """
        userservice = self.user_service_managed

        # To ready should be invoked and, in turn, ready_notified from to_ready
        for method in (TestOSManager.process_ready.__name__, TestOSManager.on_ready.__name__):
            actor_token = userservice.uuid
            userservice.set_in_use(True)
            userservice.set_os_state(types.states.State.PREPARING)
            with mock.patch.object(TestOSManager, method) as to_ready:
                response = self.client.post(
                    "/uds/rest/actor/v3/ready",
                    data={
                        "token": actor_token,
                        "ip": "1.2.3.4",
                        "secret": "test_secret",
                        "port": 43910,
                    },
                    content_type="application/json",
                )

                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertIn("result", data)
                self.assertIn("stamp", data)
                self.assertIn("version", data)
                self.assertIn("build", data)

                self.assertEqual(data["version"], consts.system.VERSION)
                self.assertEqual(data["build"], consts.system.VERSION_STAMP)

                self.assertLess(
                    abs(data["stamp"] - sql_stamp_seconds()), 2
                )  # May differ in miliseconds, but 0.999 + 0.001 = 1 :)

                result = data["result"]
                for i in ("key", "certificate", "password", "ciphers"):
                    self.assertIn(i, result)
                    self.assertIsInstance(result[i], str)
                    self.assertGreater(len(result[i]), 0 if i != "ciphers" else -1)

                userservice.refresh_from_db()
                # We are not in use anymore, because "ready" means just initialized, so we are not in use
                self.assertEqual(userservice.in_use, False)
                self.assertEqual(userservice.os_state, types.states.State.USABLE)

                to_ready.assert_called_once_with(userservice)


class ActorReadyAtomicityTest(rest.test.RESTActorTestCase):
    """The ready/ipchange flow must never persist a comms url without its
    pinned certificate (the certificate is generated before anything is
    stored), so the comms client can fail closed without certificate."""

    def _post_ready(self, userservice: "models.UserService", ip: str) -> dict[str, typing.Any]:
        response = self.client.post(
            "/uds/rest/actor/v3/ready",
            data={
                "type": "managed",
                "token": userservice.uuid,
                "ip": ip,
                "secret": "test_secret",
                "port": 43910,
            },
            content_type="application/json",
        )
        return response.json()

    def test_ready_persists_cert_and_url_together(self) -> None:
        userservice = self.user_service_managed
        with mock.patch.object(TestOSManager, "process_ready"), mock.patch.object(TestOSManager, "on_ready"):
            result = self._post_ready(userservice, "10.0.0.5")

        # The ready response is the certificate delivery itself
        cert_data: dict[str, typing.Any] = result["result"]
        self.assertTrue(cert_data.get("certificate"), result)
        self.assertTrue(cert_data.get("private_key"), result)
        userservice.refresh_from_db()
        # Comms url and certificate stored together, and certificate is non-empty
        self.assertEqual(
            userservice.properties.get("comms_url"),
            "https://10.0.0.5:43910/actor/test_secret",
        )
        self.assertTrue(userservice.properties.get("cert"))
        self.assertTrue(userservice.properties.get("priv"))
        self.assertTrue(userservice.properties.get("priv_passwd"))

    def test_ready_with_invalid_ip_persists_nothing(self) -> None:
        userservice = self.user_service_managed
        # Not a valid ip: cert generation fails before anything is stored
        result = self._post_ready(userservice, "not-an-ip")

        self.assertFalse(result.get("result", {}).get("certificate"), result)
        userservice.refresh_from_db()
        self.assertIsNone(userservice.properties.get("comms_url"))
        self.assertFalse(userservice.properties.get("cert"))
        self.assertFalse(userservice.properties.get("priv"))
