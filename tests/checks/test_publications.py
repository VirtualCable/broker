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

Tests for the stuck publications check (uds.checks.publications).
"""

import datetime

from uds import models
from uds.core import types
from uds.core.checks import runner as runner_module
from uds.core.types.states import State
from uds.core.util.model import sql_now

from ..fixtures import services as services_fixtures
from ..utils.test import UDSTransactionTestCase


class StuckPublicationsCheckTest(UDSTransactionTestCase):
    def _run_check(self) -> types.checks.CheckResult:
        results = {result.id: result for result in runner_module.run_checks(types.checks.CheckKind.AUTOMATIC)}
        self.assertIn("stuck-publications", results)
        return results["stuck-publications"]

    def _create_publication(
        self,
        state: str,
        *,
        age: datetime.timedelta = datetime.timedelta(),
    ) -> models.ServicePoolPublication:
        provider = services_fixtures.create_db_provider()
        service = services_fixtures.create_db_service(provider)
        service_pool = services_fixtures.create_db_servicepool(service)
        publication = services_fixtures.create_db_publication(service_pool)
        publication.state = state
        publication.state_date = sql_now() - age
        publication.save(update_fields=["state", "state_date"])
        return publication

    def test_passes_with_no_publications(self) -> None:
        result = self._run_check()
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.category, types.checks.CheckCategory.HEALTH)

    def test_passes_with_usable_or_fresh_transitional_publications(self) -> None:
        # A usable publication is the normal final state, never flagged
        self._create_publication(State.USABLE, age=datetime.timedelta(days=365))
        # A recently started transitional publication is within the window
        self._create_publication(State.PREPARING, age=datetime.timedelta(minutes=10))

        result = self._run_check()
        self.assertTrue(result.ok, result.message)

    def test_fails_with_stuck_preparing_publication(self) -> None:
        publication = self._create_publication(
            State.PREPARING,
            age=datetime.timedelta(hours=8),  # default max age is 4h
        )

        result = self._run_check()
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.checks.CheckSeverity.MEDIUM)
        self.assertIn(publication.deployed_service.name, result.message)
        self.assertIn("preparing", result.message)

    def test_fails_with_stuck_removing_publication(self) -> None:
        publication = self._create_publication(State.REMOVING, age=datetime.timedelta(hours=8))

        result = self._run_check()
        self.assertFalse(result.ok, result.message)
        self.assertIn(publication.deployed_service.name, result.message)
        self.assertIn("removing", result.message)

    def test_details_list_every_stuck_publication(self) -> None:
        # The message only shows a few examples; the details carry all of them
        publications = [
            self._create_publication(State.PREPARING, age=datetime.timedelta(hours=8)) for _ in range(7)
        ]

        result = self._run_check()
        self.assertFalse(result.ok, result.message)
        self.assertEqual(len(result.details), len(publications))
        for publication in publications:
            self.assertTrue(
                any(line.startswith(publication.deployed_service.name) for line in result.details),
                publication.deployed_service.name,
            )
