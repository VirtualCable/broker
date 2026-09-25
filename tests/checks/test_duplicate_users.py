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

Tests for the duplicated users manual check (uds.checks.users).
"""

from uds import models
from uds.core import types
from uds.core.checks import runner as runner_module

from ..fixtures import authenticators as authenticators_fixtures
from ..utils.test import UDSTransactionTestCase


class DuplicateUsersCheckTest(UDSTransactionTestCase):
    def _run_check(self) -> types.checks.CheckResult:
        results = {result.id: result for result in runner_module.run_checks(types.checks.CheckKind.MANUAL)}
        return results["duplicate-users-in-authenticator"]

    def _add_users(self, authenticator: models.Authenticator, *names: str) -> None:
        for name in names:
            authenticator.users.create(name=name, real_name=name, state=types.states.State.ACTIVE)

    def test_passes_with_distinct_names(self) -> None:
        self._add_users(authenticators_fixtures.create_db_authenticator(), "john", "jane")
        result = self._run_check()
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.category, types.checks.CheckCategory.HEALTH)

    def test_fails_on_names_differing_in_case_and_spacing(self) -> None:
        authenticator = authenticators_fixtures.create_db_authenticator()
        self._add_users(authenticator, "John  Smith", "john smith ")
        result = self._run_check()
        self.assertFalse(result.ok, result.message)
        self.assertIn(authenticator.name, result.message)
        self.assertIn("'John  Smith'", result.message)
        self.assertEqual(len(result.details), 1)
        self.assertTrue(result.details[0].startswith(f"{authenticator.name}: "), result.details)

    def test_fails_on_compatible_unicode_forms(self) -> None:
        # Full-width letters normalize (NFKC) to their ASCII equivalents
        self._add_users(authenticators_fixtures.create_db_authenticator(), "admin", "ａｄｍｉｎ")
        self.assertFalse(self._run_check().ok)

    def test_same_name_in_two_authenticators_is_not_a_duplicate(self) -> None:
        first = authenticators_fixtures.create_db_authenticator()
        second = models.Authenticator(name="Second", data_type=first.data_type, data=first.data)
        second.save()
        self._add_users(first, "john")
        self._add_users(second, "JOHN")
        self.assertTrue(self._run_check().ok)
