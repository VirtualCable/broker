#
# Copyright (c) 2012-2023 Virtual Cable S.L.
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

import typing

from uds import models
from uds.models.action_flow import ActionFlow, FlowAction

from tests.fixtures import authenticators as authenticators_fixtures
from tests.utils.test import UDSTestCase


class ActionFlowModelTest(UDSTestCase):
    """
    Tests for ActionFlow/FlowAction models (properties usage, helpers, cleanup)
    """

    auth: "models.Authenticator"
    user: "models.User"
    group: "models.Group"

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.auth = authenticators_fixtures.create_db_authenticator()
        self.group = authenticators_fixtures.create_db_groups(self.auth, 1)[0]
        self.user = authenticators_fixtures.create_db_users(self.auth, 1, groups=[self.group])[0]

    def test_owner_display_falls_back_to_cached_name(self) -> None:
        flow = ActionFlow.objects.create(name="flow 1")
        flow.properties["owner_name"] = "agent-01"

        self.assertEqual(flow.owner_display, "agent-01")

    def test_owner_display_uses_live_user(self) -> None:
        flow = ActionFlow.objects.create(name="flow 1", owner=self.user)
        flow.properties["owner_name"] = "stale"

        self.assertEqual(flow.owner_display, str(self.user.name))

    def test_snap_info_roundtrip(self) -> None:
        flow = ActionFlow.objects.create(name="flow 1")
        action = FlowAction.objects.create(
            flow=flow,
            order=0,
            action_type="provider.update",
            target_kind="provider",
            target_uuid="abcd",
        )
        action.snap_info = {"target_name": "OpenStack prod", "current_values": {"name": "x"}}

        reloaded = FlowAction.objects.get(uuid=action.uuid)
        self.assertEqual(reloaded.snap_info["target_name"], "OpenStack prod")
        self.assertEqual(reloaded.snap_info["current_values"], {"name": "x"})

    def test_snap_info_default_empty(self) -> None:
        flow = ActionFlow.objects.create(name="flow 1")
        action = FlowAction.objects.create(flow=flow, order=0, action_type="provider.update")

        self.assertEqual(action.snap_info, {})

    def test_delete_flow_clears_properties(self) -> None:
        flow = ActionFlow.objects.create(name="flow 1")
        flow.properties["owner_name"] = "agent-01"
        action = FlowAction.objects.create(flow=flow, order=0, action_type="provider.update")
        action.snap_info = {"target_name": "x"}

        flow_uuid, action_uuid = str(flow.uuid), str(action.uuid)
        flow.delete()

        self.assertFalse(models.Properties.objects.filter(owner_id=flow_uuid, owner_type="actionflow").exists())
        self.assertFalse(models.Properties.objects.filter(owner_id=action_uuid, owner_type="flowaction").exists())

    def test_order_unique_per_flow(self) -> None:
        flow = ActionFlow.objects.create(name="flow 1")
        FlowAction.objects.create(flow=flow, order=0, action_type="provider.update")
        FlowAction.objects.create(flow=flow, order=1, action_type="provider.update")
        other = ActionFlow.objects.create(name="flow 2")
        FlowAction.objects.create(flow=other, order=0, action_type="provider.update")  # Same order, other flow

        with self.assertRaises(Exception):
            FlowAction.objects.create(flow=flow, order=0, action_type="provider.update")
