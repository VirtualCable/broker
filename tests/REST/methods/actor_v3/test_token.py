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
Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import logging

from uds.core import consts

from ....utils import rest

logger = logging.getLogger(__name__)


class ActorTokenTest(rest.test.RESTActorTestCase):
    """
    Test actor token functionality (dual lookup, rotation, exposure)
    """

    def test_default_token_has_prefix(self) -> None:
        """
        Test that default token generator produces a 48-char token with AUTO_TOKEN_PREFIX_NOT_USED
        """
        user_service = self.user_service_managed

        self.assertEqual(len(user_service.token), consts.ticket.TICKET_LENGTH)
        self.assertTrue(user_service.token.startswith(consts.auth.AUTO_TOKEN_PREFIX_NOT_USED))

    def test_actor_token_has_prefix(self) -> None:
        """
        Test that actor token generator produces a 48-char token with USER_SERVICE_TOKEN_PREFIX
        """
        from uds.models.user_service import create_actor_token

        token = create_actor_token()

        self.assertEqual(len(token), consts.ticket.TICKET_LENGTH)
        self.assertTrue(token.startswith(consts.auth.USER_SERVICE_TOKEN_PREFIX))
        self.assertFalse(token.startswith(consts.auth.AUTO_TOKEN_PREFIX_NOT_USED))

    def test_dual_lookup_by_token(self) -> None:
        """
        Test that actor_v3 can lookup userservice by token
        """
        userservice = self.user_service_managed
        actor_token = userservice.token

        response = self.client.post(
            "/uds/rest/actor/v3/version",
            data={
                "token": actor_token,
                "version": consts.system.VERSION,
                "ip": "1.2.3.4",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

    def test_dual_lookup_by_uuid(self) -> None:
        """
        Test that actor_v3 can lookup userservice by uuid (backward compat)
        """
        userservice = self.user_service_managed
        actor_token = userservice.uuid

        response = self.client.post(
            "/uds/rest/actor/v3/version",
            data={
                "token": actor_token,
                "version": consts.system.VERSION,
                "ip": "1.2.3.4",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

    def test_dual_lookup_unknown_blocked(self) -> None:
        """
        Test that actor_v3 blocks requests with unknown token/uuid
        """
        response = self.client.post(
            "/uds/rest/actor/v3/version",
            data={
                "token": "unknown_token_or_uuid",
                "version": consts.system.VERSION,
                "ip": "1.2.3.4",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    def test_token_rotation_on_initialize(self) -> None:
        """
        Test that initialize rotates the token and returns the new one
        """
        user_service = self.user_service_managed
        old_token = user_service.token

        self.assertTrue(old_token.startswith(consts.auth.AUTO_TOKEN_PREFIX_NOT_USED))

        actor_token = self.login_and_register()
        unique_id = user_service.get_unique_id()

        response = self.client.post(
            "/uds/rest/actor/v3/initialize",
            data={
                "type": "managed",
                "version": consts.system.VERSION,
                "token": actor_token,
                "id": [{"mac": unique_id, "ip": "1.2.3.4"}],
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        result = data["result"]

        user_service.refresh_from_db()
        new_token = user_service.token

        self.assertNotEqual(old_token, new_token)
        self.assertEqual(result["token"], new_token)
        self.assertEqual(result["own_token"], new_token)
        self.assertTrue(new_token.startswith(consts.auth.USER_SERVICE_TOKEN_PREFIX))
        self.assertFalse(new_token.startswith(consts.auth.AUTO_TOKEN_PREFIX_NOT_USED))

    def test_old_token_invalid_after_rotation(self) -> None:
        """
        Test that after initialize, the old token no longer works
        """
        user_service = self.user_service_managed
        old_token = user_service.token

        actor_token = self.login_and_register()
        unique_id = user_service.get_unique_id()

        response = self.client.post(
            "/uds/rest/actor/v3/initialize",
            data={
                "type": "managed",
                "version": consts.system.VERSION,
                "token": actor_token,
                "id": [{"mac": unique_id, "ip": "1.2.3.4"}],
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            "/uds/rest/actor/v3/version",
            data={
                "token": old_token,
                "version": consts.system.VERSION,
                "ip": "1.2.3.4",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    def test_new_token_works_after_rotation(self) -> None:
        """
        Test that after initialize, the new token works
        """
        user_service = self.user_service_managed

        actor_token = self.login_and_register()
        unique_id = user_service.get_unique_id()

        response = self.client.post(
            "/uds/rest/actor/v3/initialize",
            data={
                "type": "managed",
                "version": consts.system.VERSION,
                "token": actor_token,
                "id": [{"mac": unique_id, "ip": "1.2.3.4"}],
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        new_token = data["result"]["token"]

        response = self.client.post(
            "/uds/rest/actor/v3/version",
            data={
                "token": new_token,
                "version": consts.system.VERSION,
                "ip": "1.2.3.4",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
