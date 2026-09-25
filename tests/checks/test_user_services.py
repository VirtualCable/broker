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
Author: Andres Schumann, aschumann at virtualcable dot es

Tests for the user service checks (uds.checks.user_services).
"""

import datetime

from uds import models
from uds.core import types
from uds.core.checks import runner as runner_module
from uds.core.types.states import State
from uds.core.util.model import sql_now

from ..fixtures import services as services_fixtures
from ..utils.test import UDSTransactionTestCase


class UserServicesChecksTest(UDSTransactionTestCase):
    def _run_check(self, check_id: str) -> types.checks.CheckResult:
        results = {result.id: result for result in runner_module.run_checks(types.checks.CheckKind.AUTOMATIC)}
        return results[check_id]

    def _create_userservice(self, **fields: object) -> models.UserService:
        provider = services_fixtures.create_db_provider()
        service_pool = services_fixtures.create_db_servicepool(services_fixtures.create_db_service(provider))
        userservice = services_fixtures.create_db_userservice(
            service_pool, services_fixtures.create_db_publication(service_pool)
        )
        for name, value in fields.items():
            setattr(userservice, name, value)
        userservice.save()
        return userservice

    def test_stuck_preparing_passes_with_fresh_preparing(self) -> None:
        self._create_userservice(state=State.PREPARING, state_date=sql_now())
        result = self._run_check("user-services-stuck-preparing")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.category, types.checks.CheckCategory.HEALTH)

    def test_stuck_preparing_fails_on_old_preparing(self) -> None:
        userservice = self._create_userservice(
            state=State.PREPARING, state_date=sql_now() - datetime.timedelta(hours=5)
        )
        result = self._run_check("user-services-stuck-preparing")
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.checks.CheckSeverity.HIGH)
        self.assertIn(userservice.deployed_service.name, result.message)
        self.assertEqual(result.details, (f"{userservice.deployed_service.name} (1)",))

    def test_stuck_preparing_fails_when_actor_never_reported(self) -> None:
        self._create_userservice(
            state=State.USABLE, os_state=State.PREPARING, state_date=sql_now() - datetime.timedelta(hours=5)
        )
        self.assertFalse(self._run_check("user-services-stuck-preparing").ok)

    def test_stale_passes_with_recently_used_service(self) -> None:
        old = sql_now() - datetime.timedelta(days=200)
        self._create_userservice(creation_date=old, in_use_date=sql_now() - datetime.timedelta(days=1))
        self.assertTrue(self._run_check("stale-user-services").ok)

    def test_stale_fails_with_unused_assigned_service(self) -> None:
        old = sql_now() - datetime.timedelta(days=200)
        userservice = self._create_userservice(creation_date=old, in_use_date=old)
        result = self._run_check("stale-user-services")
        self.assertFalse(result.ok, result.message)
        self.assertIn(userservice.deployed_service.name, result.message)

    def test_stale_ignores_cached_services(self) -> None:
        old = sql_now() - datetime.timedelta(days=200)
        self._create_userservice(creation_date=old, in_use_date=old, cache_level=1)
        self.assertTrue(self._run_check("stale-user-services").ok)
