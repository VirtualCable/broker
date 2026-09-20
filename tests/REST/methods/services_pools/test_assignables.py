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
Contract tests for the service pool assignables custom methods.

Freezes what the clients (GUI, and the MCP proposal tool built on top)
rely on from ``GET /servicespools/{uuid}/list_assignables`` and
``POST /servicespools/{uuid}/create_from_assignable``: the dispatching,
the parameter validation, the error mapping, and the answer shape.

The success path mocks the manager boundary on purpose: these tests pin
the REST contract, not the service semantics (the manager's own behavior
is exercised where it is defined). That also keeps the tests free from
real VM interactions and delayed-task side effects.

Reference: src/uds/REST/methods/services_pools.py

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import typing
import uuid as uuid_module
from unittest import mock

from django.utils import timezone

from uds import models
from uds.core import types
from uds.core.managers.userservice import UserServiceManager

from tests.fixtures import services as services_fixtures
from tests.fixtures.modules.service.service import TestServiceNoCache
from tests.utils import rest

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


class AssignablesCustomMethodsTest(rest.test.RESTTestCase):
    """External contract of the pool assignables custom methods."""

    pool: models.ServicePool

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        # A pool on a service type without publications: the assignable
        # creation then goes through the "from pool" branch, no active
        # publication needed.
        service = services_fixtures.create_db_service(self.provider, use_caching_version=False)
        self.pool = services_fixtures.create_db_servicepool(service, groups=self.groups)

    def _created_userservice(self, user: models.User) -> models.UserService:
        """An unsaved UserService, as the manager would return on success."""
        return models.UserService(
            deployed_service=self.pool,
            user=user,
            state=types.states.State.PREPARING,
            os_state=types.states.State.PREPARING,
            state_date=timezone.localtime(),
            creation_date=timezone.localtime(),
            cache_level=0,
        )

    # ------------------------------------------------------------------
    # list_assignables
    # ------------------------------------------------------------------
    def test_list_assignables_answers_the_service_choices(self) -> None:
        choices = [
            types.ui.ChoiceItem("m-1", "Machine 1"),
            types.ui.ChoiceItem("m-2", "Machine 2"),
        ]
        with mock.patch.object(TestServiceNoCache, "enumerate_assignables", return_value=choices):
            response = self.client.rest_get(f"servicespools/{self.pool.uuid}/list_assignables")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response.json(),
            [{"id": "m-1", "text": "Machine 1"}, {"id": "m-2", "text": "Machine 2"}],
        )

    def test_list_assignables_unknown_pool_is_not_found(self) -> None:
        response = self.client.rest_get(f"servicespools/{uuid_module.uuid4()}/list_assignables")
        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------
    # create_from_assignable — validation and error mapping
    # ------------------------------------------------------------------
    def test_create_requires_both_params(self) -> None:
        for params in ({}, {"user_id": self.plain_users[0].uuid}, {"assignable_id": "m-1"}):
            with self.subTest(params=params):
                response = self.client.rest_post(
                    f"servicespools/{self.pool.uuid}/create_from_assignable", data=params
                )
                self.assertEqual(response.status_code, 400, response.content)

    def test_create_unknown_user_is_not_found(self) -> None:
        response = self.client.rest_post(
            f"servicespools/{self.pool.uuid}/create_from_assignable",
            data={"user_id": "00000000-0000-0000-0000-000000000000", "assignable_id": "m-1"},
        )
        self.assertEqual(response.status_code, 404)

    def test_create_unknown_pool_is_not_found(self) -> None:
        response = self.client.rest_post(
            f"servicespools/{self.pool.service.uuid}/create_from_assignable",
            data={"user_id": self.plain_users[0].uuid, "assignable_id": "m-1"},
        )
        self.assertEqual(response.status_code, 404)

    def test_create_on_a_not_assignable_type_fails(self) -> None:
        # The manager raises for service types that do not override the
        # assignables API; the dispatcher maps it to a server error. The
        # MCP proposal layer is expected to validate this at propose time
        # so this never reaches REST.
        user = self.plain_users[0]
        response = self.client.rest_post(
            f"servicespools/{self.pool.uuid}/create_from_assignable",
            data={"user_id": user.uuid, "assignable_id": "m-1"},
        )
        self.assertEqual(response.status_code, 500)
        self.assertFalse(models.UserService.objects.filter(user=user, deployed_service=self.pool).exists())

    # ------------------------------------------------------------------
    # create_from_assignable — success path (manager mocked)
    # ------------------------------------------------------------------
    def test_create_dispatches_to_the_manager(self) -> None:
        user = self.plain_users[0]
        created = self._created_userservice(user)
        with mock.patch.object(UserServiceManager, "create_from_assignable", return_value=created) as manager:
            response = self.client.rest_post(
                f"servicespools/{self.pool.uuid}/create_from_assignable",
                data={"user_id": user.uuid, "assignable_id": "m-1"},
            )
        self.assertEqual(response.status_code, 200, response.content)
        manager.assert_called_once()
        args = manager.call_args.args
        self.assertEqual(args[0], self.pool)
        self.assertEqual(args[1], user)
        self.assertEqual(args[2], "m-1")
