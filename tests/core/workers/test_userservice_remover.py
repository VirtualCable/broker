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

import datetime
from unittest import mock

from django.utils import timezone

from uds import models
from uds.core import types
from uds.core.environment import Environment
from uds.core.types.states import State
from uds.workers.userservice_cleaner import UserServiceRemover

from ...fixtures import services as services_fixtures
from ...fixtures.modules.service.deployment import TestUserService
from ...fixtures.modules.service.provider import TestProvider
from ...utils.test import UDSTestCase


class UserServiceRemoverTest(UDSTestCase):
    service_pool: models.ServicePool

    def setUp(self) -> None:
        super().setUp()
        # Keep destroyed userservices in REMOVING so they count against the limit
        self.enterContext(
            mock.patch.object(TestUserService, "destroy", return_value=types.states.TaskState.RUNNING)
        )
        provider = services_fixtures.create_db_provider()
        self.service_pool = services_fixtures.create_db_servicepool(
            services_fixtures.create_db_service(provider)
        )
        publication = services_fixtures.create_db_publication(self.service_pool)
        for _ in range(6):
            userservice = services_fixtures.create_db_userservice(self.service_pool, publication)
            userservice.state = State.REMOVABLE
            userservice.state_date = timezone.localtime() - datetime.timedelta(minutes=5)
            userservice.save(update_fields=["state", "state_date"])

    def set_removal_limit(self, limit: int) -> None:
        self.enterContext(mock.patch.object(TestProvider, "concurrent_removal_limit", limit))

    def run_remover(self) -> None:
        UserServiceRemover(Environment.testing_environment()).run()

    def count_in_state(self, state: str) -> int:
        return self.service_pool.userServices.filter(state=state).count()

    def test_limit_of_one_removes_a_single_userservice_per_pass(self) -> None:
        self.set_removal_limit(1)

        self.run_remover()

        self.assertEqual(self.count_in_state(State.REMOVING), 1)
        self.assertEqual(self.count_in_state(State.REMOVABLE), 5)

    def test_limit_is_honoured_within_a_single_pass(self) -> None:
        self.set_removal_limit(3)

        self.run_remover()

        self.assertEqual(self.count_in_state(State.REMOVING), 3)
        self.assertEqual(self.count_in_state(State.REMOVABLE), 3)

    def test_next_pass_waits_while_limit_is_reached(self) -> None:
        self.set_removal_limit(1)

        self.run_remover()
        self.run_remover()

        self.assertEqual(self.count_in_state(State.REMOVING), 1)
        self.assertEqual(self.count_in_state(State.REMOVABLE), 5)

    def test_next_pass_resumes_once_a_removal_finishes(self) -> None:
        self.set_removal_limit(1)
        self.run_remover()
        self.service_pool.userServices.filter(state=State.REMOVING).update(state=State.REMOVED)

        self.run_remover()

        self.assertEqual(self.count_in_state(State.REMOVED), 1)
        self.assertEqual(self.count_in_state(State.REMOVING), 1)
        self.assertEqual(self.count_in_state(State.REMOVABLE), 4)
