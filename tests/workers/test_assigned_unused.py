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

import datetime
from unittest import mock

from uds import models
from uds.core.environment import Environment
from uds.core.types.states import State
from uds.core.util import config, model
from uds.workers import assigned_unused as assigned_unused_module
from uds.workers.assigned_unused import AssignedAndUnused

from ..fixtures import services as fixtures_services
from ..utils.test import UDSTestCase


class AssignedAndUnusedLogoutTest(UDSTestCase):
    """
    Verify the no-osmanager branch of `AssignedAndUnused` follows the
    actor logout flow: it must call `OSManager.logged_out` and delegate the
    removal/cache decision to `UserServiceManager.release_from_logout`
    instead of unconditionally releasing the user service.
    """

    user_services: list[models.UserService]

    def setUp(self) -> None:
        config.GlobalConfig.CHECK_UNUSED_TIME.set("600")
        # Unmanaged → service pools without osmanager (the branch we are testing)
        self.user_services = fixtures_services.create_db_assigned_userservices(count=4, type_="unmanaged")

    def _expire(self, user_services: list[models.UserService]) -> None:
        for us in user_services:
            us.state_date = model.sql_now() - datetime.timedelta(seconds=602)
            us.save(update_fields=["state_date"])

    def test_recent_unused_does_nothing(self) -> None:
        """
        Recently-updated user services must not be touched by the worker.
        """
        for us in self.user_services:
            us.set_state(State.USABLE)

        with (
            mock.patch.object(assigned_unused_module.osmanagers.OSManager, "logged_out") as logged_out,
            mock.patch.object(assigned_unused_module.UserServiceManager, "manager") as manager_factory,
        ):
            AssignedAndUnused(Environment.testing_environment()).run()

        logged_out.assert_not_called()
        manager_factory.assert_not_called()

    def test_expired_unused_triggers_logout_flow(self) -> None:
        """
        Expired user services without osmanager must follow the logout flow:
        `OSManager.logged_out` is invoked, then `release_from_logout` decides
        whether to send back to cache or to release.
        """
        self._expire(self.user_services)

        with (
            mock.patch.object(assigned_unused_module.osmanagers.OSManager, "logged_out") as logged_out,
            mock.patch.object(assigned_unused_module.UserServiceManager, "manager") as manager_factory,
        ):
            release_from_logout = manager_factory.return_value.release_from_logout

            AssignedAndUnused(Environment.testing_environment()).run()

        self.assertEqual(logged_out.call_count, len(self.user_services))
        self.assertEqual(release_from_logout.call_count, len(self.user_services))

        logged_out_targets = {call.args[0].pk for call in logged_out.call_args_list}
        release_targets = {call.args[0].pk for call in release_from_logout.call_args_list}
        expected = {us.pk for us in self.user_services}

        self.assertEqual(logged_out_targets, expected)
        self.assertEqual(release_targets, expected)

        for call in logged_out.call_args_list:
            self.assertEqual(call.kwargs.get("username"), "unused")

    def test_expired_unused_release_from_logout_releases(self) -> None:
        """
        End-to-end check: when the service does not allow putting back to
        cache, `release_from_logout` falls through to `release()` and the
        user services end up in REMOVABLE state.
        """
        self._expire(self.user_services)

        before = models.UserService.objects.filter(state=State.REMOVABLE).count()
        AssignedAndUnused(Environment.testing_environment()).run()
        after = models.UserService.objects.filter(state=State.REMOVABLE).count()

        self.assertEqual(after - before, len(self.user_services))
