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
Tests that the randomly generated VM passwords are masked on the user
service log: the plaintext lives on the user service properties and is
delivered to the actor, but must not linger on the (staff readable)
service log.
"""

from tests.fixtures import authenticators as authenticators_fixtures
from tests.fixtures import services as services_fixtures
from tests.utils.test import UDSTestCase
from uds import models
from uds.core.environment import Environment
from uds.core.util import log
from uds.osmanagers.LinuxOsManager import linux_randompass_osmanager
from uds.osmanagers.WindowsOsManager import windows_random


class RandomPassLogMaskingTest(UDSTestCase):
    def _userservice(self) -> "models.UserService":
        provider = services_fixtures.create_db_provider()
        auth = authenticators_fixtures.create_db_authenticator()
        groups = authenticators_fixtures.create_db_groups(auth)
        users = authenticators_fixtures.create_db_users(auth, number_of_users=1, groups=groups)
        return services_fixtures.create_db_one_assigned_userservice(provider, users[0], groups, "managed")

    def _assert_password_masked(self, userservice: "models.UserService", password: str) -> None:
        entries = log.get_logs(userservice)
        self.assertTrue(any("Password set to" in entry["message"] for entry in entries), entries)
        for entry in entries:
            self.assertNotIn(password, entry["message"])

    def test_linux_random_password_is_masked_on_log(self) -> None:
        userservice = self._userservice()
        osmanager_instance = linux_randompass_osmanager.LinuxRandomPassManager(
            environment=Environment.testing_environment()
        )
        password = osmanager_instance.gen_random_password(userservice)
        self.assertTrue(password)
        self._assert_password_masked(userservice, password)

    def test_windows_random_password_is_masked_on_log(self) -> None:
        userservice = self._userservice()
        osmanager_instance = windows_random.WinRandomPassManager(environment=Environment.testing_environment())
        password = osmanager_instance.gen_random_password(userservice)
        self.assertTrue(password)
        self._assert_password_masked(userservice, password)
