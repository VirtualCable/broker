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
Author: Andres Schumann, aschumann at virtualcable dot es
"""

import typing

from django.db import connection

from uds import models
from uds.core.util.model import sql_now
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

    def test_execution_audit_roundtrip(self) -> None:
        """executed_by/executed_at wrappers persist on Properties (flow and action)."""
        flow = ActionFlow.objects.create(name="flow 1")
        action = FlowAction.objects.create(flow=flow, order=0, action_type="provider.update")
        stamp = sql_now()

        flow.executed_by = "admin-01"
        flow.executed_at = stamp
        action.executed_by = "admin-01"
        action.executed_at = stamp

        reloaded_flow = ActionFlow.objects.get(uuid=flow.uuid)
        reloaded_action = FlowAction.objects.get(uuid=action.uuid)
        self.assertEqual(reloaded_flow.executed_by, "admin-01")
        self.assertEqual(reloaded_flow.executed_at, stamp)
        self.assertEqual(reloaded_action.executed_by, "admin-01")
        self.assertEqual(reloaded_action.executed_at, stamp)

    def test_execution_audit_defaults(self) -> None:
        """Never-launched flows/actions report an empty audit."""
        flow = ActionFlow.objects.create(name="flow 1")
        action = FlowAction.objects.create(flow=flow, order=0, action_type="provider.update")

        self.assertEqual(flow.executed_by, "")
        self.assertIsNone(flow.executed_at)
        self.assertEqual(action.executed_by, "")
        self.assertIsNone(action.executed_at)

    def test_delete_flow_clears_properties(self) -> None:
        flow = ActionFlow.objects.create(name="flow 1")
        flow.properties["owner_name"] = "agent-01"
        action = FlowAction.objects.create(flow=flow, order=0, action_type="provider.update")
        action.snap_info = {"target_name": "x"}

        flow_uuid, action_uuid = str(flow.uuid), str(action.uuid)
        flow.delete()

        self.assertFalse(models.Properties.objects.filter(owner_id=flow_uuid, owner_type="actionflow").exists())
        self.assertFalse(
            models.Properties.objects.filter(owner_id=action_uuid, owner_type="flowaction").exists()
        )

    def test_order_unique_per_flow(self) -> None:
        flow = ActionFlow.objects.create(name="flow 1")
        FlowAction.objects.create(flow=flow, order=0, action_type="provider.update")
        FlowAction.objects.create(flow=flow, order=1, action_type="provider.update")
        other = ActionFlow.objects.create(name="flow 2")
        FlowAction.objects.create(flow=other, order=0, action_type="provider.update")  # Same order, other flow

        with self.assertRaises(Exception):
            FlowAction.objects.create(flow=flow, order=0, action_type="provider.update")

    def _raw_values_column(self, action: FlowAction) -> str:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {connection.ops.quote_name('values')} FROM uds_flow_action WHERE id = %s", [action.pk]
            )
            return str(cursor.fetchone()[0])

    def _raw_property(self, action: FlowAction, key: str) -> typing.Any:
        return models.Properties.objects.get(owner_id=action.uuid, owner_type="flowaction", key=key).value

    def test_values_and_snapshots_are_encrypted_at_rest(self) -> None:
        """Nothing a flow stores (payload, CAS snapshots, review cache) is readable from the database."""
        flow = ActionFlow.objects.create(name="flow 1")
        secret = {"name": "prov", "password": "s3cr3t-value"}
        action = FlowAction.objects.create(flow=flow, order=0, action_type="provider.update", values=secret)
        action.base_values = secret
        action.approved_values = secret
        action.snap_info = {"current_values": secret}

        self.assertNotIn("s3cr3t-value", self._raw_values_column(action))
        for key in ("base_values", "approved_values", "snap_info"):
            raw = self._raw_property(action, key)
            self.assertIsInstance(raw, str)
            self.assertNotIn("s3cr3t-value", raw)

        reloaded = FlowAction.objects.get(uuid=action.uuid)
        self.assertEqual(reloaded.values, secret)
        self.assertEqual(reloaded.base_values, secret)
        self.assertEqual(reloaded.approved_values, secret)
        self.assertEqual(reloaded.snap_info, {"current_values": secret})

    def test_null_values_stay_null(self) -> None:
        flow = ActionFlow.objects.create(name="flow 1")
        action = FlowAction.objects.create(flow=flow, order=0, action_type="provider.delete")

        self.assertTrue(FlowAction.objects.filter(pk=action.pk, values__isnull=True).exists())
        self.assertIsNone(FlowAction.objects.get(pk=action.pk).values)
