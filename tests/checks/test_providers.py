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

Tests for the provider checks (uds.checks.providers).
"""

from django.utils import timezone

from uds import models
from uds.core import types
from uds.core.checks import runner as runner_module

from ..fixtures import services as services_fixtures
from ..utils.test import UDSTransactionTestCase


def _log_userservice_errors(userservice: models.UserService, count: int) -> None:
    for i in range(count):
        models.Log.objects.create(
            owner_id=userservice.id,
            owner_type=types.log.LogObjectType.USERSERVICE,
            created=timezone.now(),
            source=types.log.LogSource.SERVICE,
            level=types.log.LogLevel.ERROR,
            data=f"Error creating machine {i}",
        )


class ProvidersChecksTest(UDSTransactionTestCase):
    def _run_check(self, check_id: str) -> types.checks.CheckResult:
        results = {result.id: result for result in runner_module.run_checks(types.checks.CheckKind.AUTOMATIC)}
        return results[check_id]

    def _create_userservice(self) -> models.UserService:
        provider = services_fixtures.create_db_provider()
        service_pool = services_fixtures.create_db_servicepool(services_fixtures.create_db_service(provider))
        return services_fixtures.create_db_userservice(
            service_pool, services_fixtures.create_db_publication(service_pool)
        )

    def test_provider_errors_pass_below_threshold(self) -> None:
        _log_userservice_errors(self._create_userservice(), 9)
        self.assertTrue(self._run_check("provider-errors-24h").ok)

    def test_provider_errors_fail_at_threshold(self) -> None:
        userservice = self._create_userservice()
        _log_userservice_errors(userservice, 10)
        result = self._run_check("provider-errors-24h")
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.checks.CheckSeverity.HIGH)
        self.assertIn(f"{userservice.deployed_service.service.provider.name} (10)", result.message)

    def test_provider_errors_ignore_old_entries(self) -> None:
        userservice = self._create_userservice()
        _log_userservice_errors(userservice, 10)
        models.Log.objects.update(created=timezone.now() - timezone.timedelta(days=2))
        self.assertTrue(self._run_check("provider-errors-24h").ok)

    def test_maintenance_passes_without_maintenance(self) -> None:
        services_fixtures.create_db_provider()
        self.assertTrue(self._run_check("provider-in-maintenance-mode").ok)

    def test_maintenance_lists_providers_in_maintenance(self) -> None:
        provider = services_fixtures.create_db_provider()
        provider.maintenance_mode = True
        provider.save(update_fields=["maintenance_mode"])
        result = self._run_check("provider-in-maintenance-mode")
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.checks.CheckSeverity.LOW)
        self.assertIn(provider.name, result.message)
