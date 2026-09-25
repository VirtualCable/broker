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

Tests for the domain join check (uds.checks.osmanagers).
"""

from django.utils import timezone

from uds import models
from uds.core import types
from uds.core.checks import runner as runner_module

from ..fixtures import services as services_fixtures
from ..utils.test import UDSTransactionTestCase


class DomainJoinFailuresCheckTest(UDSTransactionTestCase):
    def _run_check(self) -> types.checks.CheckResult:
        results = {result.id: result for result in runner_module.run_checks(types.checks.CheckKind.AUTOMATIC)}
        return results["domain-join-failures-24h"]

    def _log_actor(
        self, message: str, level: types.log.LogLevel = types.log.LogLevel.ERROR
    ) -> models.UserService:
        provider = services_fixtures.create_db_provider()
        service_pool = services_fixtures.create_db_servicepool(services_fixtures.create_db_service(provider))
        userservice = services_fixtures.create_db_userservice(
            service_pool, services_fixtures.create_db_publication(service_pool)
        )
        models.Log.objects.create(
            owner_id=userservice.id,
            owner_type=types.log.LogObjectType.USERSERVICE,
            created=timezone.now(),
            source=types.log.LogSource.ACTOR,
            level=level,
            data=message,
        )
        return userservice

    def test_passes_without_join_errors(self) -> None:
        self._log_actor("Error renaming computer")
        self.assertTrue(self._run_check().ok)

    def test_ignores_join_messages_below_error(self) -> None:
        self._log_actor("NetJoinDomain for 'corp.local' succeeded", types.log.LogLevel.INFO)
        self.assertTrue(self._run_check().ok)

    def test_fails_on_windows_5_actor_error(self) -> None:
        userservice = self._log_actor(
            "NetJoinDomain for domain 'corp.local', OU '', account 'joiner' failed: Access is denied"
        )
        result = self._run_check()
        self.assertFalse(result.ok, result.message)
        self.assertIn(userservice.deployed_service.name, result.message)
        self.assertEqual(result.details, (f"{userservice.deployed_service.name} (1)",))

    def test_fails_on_4_actor_error(self) -> None:
        self._log_actor("Error joining domain: 1326, The user name or password is incorrect")
        self.assertFalse(self._run_check().ok)
