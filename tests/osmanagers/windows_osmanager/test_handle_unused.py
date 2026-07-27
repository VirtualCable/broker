# -*- coding: utf-8 -*-
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

from unittest import mock

from tests.utils.test import UDSTestCase
from uds.core.environment import Environment
from uds.osmanagers.WindowsOsManager import windows as osmanager


class WindowsOsManagerHandleUnusedTest(UDSTestCase):
    """
    `handle_unused` must follow the actor logout flow when the userservice is
    removable on logout: call `OSManager.logged_out(..., username='unused')` and
    delegate the cache/release decision to `UserServiceManager.release_from_logout`.
    """

    def _make_instance(self, on_logout: str) -> "osmanager.WindowsOsManager":
        instance = osmanager.WindowsOsManager(environment=Environment.testing_environment())
        instance.on_logout.value = on_logout
        return instance

    def _make_userservice(self, *, in_use: bool, publication_valid: bool) -> mock.MagicMock:
        userservice = mock.MagicMock()
        userservice.in_use = in_use
        userservice.is_publication_valid.return_value = publication_valid
        return userservice

    def _run(
        self, instance: "osmanager.WindowsOsManager", userservice: mock.MagicMock
    ) -> tuple[mock.MagicMock, mock.MagicMock]:
        with (
            mock.patch.object(osmanager.osmanagers.OSManager, "logged_out") as logged_out,
            mock.patch.object(osmanager.UserServiceManager, "manager") as manager_factory,
            mock.patch.object(osmanager.log, "log"),
        ):
            instance.handle_unused(userservice)

        return logged_out, manager_factory.return_value.release_from_logout

    def test_in_use_does_nothing(self) -> None:
        instance = self._make_instance("remove")
        userservice = self._make_userservice(in_use=True, publication_valid=True)

        logged_out, release_from_logout = self._run(instance, userservice)

        logged_out.assert_not_called()
        release_from_logout.assert_not_called()

    def test_remove_triggers_logout_flow(self) -> None:
        instance = self._make_instance("remove")
        userservice = self._make_userservice(in_use=False, publication_valid=True)

        logged_out, release_from_logout = self._run(instance, userservice)

        logged_out.assert_called_once_with(userservice, username="unused")
        release_from_logout.assert_called_once_with(userservice)

    def test_keep_with_invalid_publication_triggers_logout_flow(self) -> None:
        instance = self._make_instance("keep")
        userservice = self._make_userservice(in_use=False, publication_valid=False)

        logged_out, release_from_logout = self._run(instance, userservice)

        logged_out.assert_called_once_with(userservice, username="unused")
        release_from_logout.assert_called_once_with(userservice)

    def test_keep_with_valid_publication_does_nothing(self) -> None:
        instance = self._make_instance("keep")
        userservice = self._make_userservice(in_use=False, publication_valid=True)

        logged_out, release_from_logout = self._run(instance, userservice)

        logged_out.assert_not_called()
        release_from_logout.assert_not_called()

    def test_keep_always_does_nothing(self) -> None:
        instance = self._make_instance("keep-always")
        userservice = self._make_userservice(in_use=False, publication_valid=False)

        logged_out, release_from_logout = self._run(instance, userservice)

        logged_out.assert_not_called()
        release_from_logout.assert_not_called()
