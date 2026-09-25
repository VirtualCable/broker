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

Tests for the cluster node registry (uds.core.util.cluster).
"""

import typing
from unittest import mock

from django.conf import settings

from uds import models
from uds.core import consts
from uds.core.util import cluster

from ...utils.test import UDSTransactionTestCase


class _IfaceStub:
    """Minimal stub of the interface info returned by get_first_iface."""

    def __init__(self, ip: str, mac: str) -> None:
        self.ip = ip
        self.mac = mac


class ClusterInfoTest(UDSTransactionTestCase):
    def _store(self, *, hostname: str = "node1.example.com") -> None:
        with (
            mock.patch("uds.core.util.cluster.socket.getfqdn", return_value=hostname),
            mock.patch(
                "uds.core.util.cluster.get_first_iface",
                return_value=_IfaceStub("10.0.0.1", "02:00:00:00:00:01"),
            ),
        ):
            cluster.store_cluster_info()

    def _row(self, key: str) -> models.Properties:
        row = models.Properties.objects.filter(owner_type="cluster", key=key).first()
        assert row is not None
        return row

    def test_store_creates_property_with_timezone_and_offset(self) -> None:
        self._store()

        row = self._row("node1.example.com|10.0.0.1")
        self.assertIn("last_seen", row.value)
        self.assertEqual(row.value["mac"], "02:00:00:00:00:01")
        self.assertEqual(row.value["timezone"], settings.TIME_ZONE)
        self.assertIsInstance(row.value["db_offset"], float)

    def test_store_updates_existing_property(self) -> None:
        self._store()
        self._store()

        rows = models.Properties.objects.filter(owner_type="cluster")
        self.assertEqual(rows.count(), 1)
        # After an update, all fields are present
        self.assertEqual(rows.first().value["timezone"], settings.TIME_ZONE)  # type: ignore[union-attr]

    def test_enumerate_returns_stored_node(self) -> None:
        self._store()

        nodes = cluster.enumerate_cluster_nodes()
        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        self.assertEqual(node.hostname, "node1.example.com")
        self.assertEqual(node.ip, "10.0.0.1")
        self.assertEqual(node.mac, "02:00:00:00:00:01")
        self.assertEqual(node.timezone, settings.TIME_ZONE)
        self.assertIsInstance(node.db_offset, float)

    def test_enumerate_legacy_row_uses_fallback_timezone(self) -> None:
        # A row stored by an older version has no timezone nor db_offset
        models.Properties.objects.create(
            owner_id="cluster",
            owner_type="cluster",
            key="legacy.example.com|10.0.0.9",
            value={"last_seen": "2026-01-01T00:00:00+00:00"},
        )

        nodes = cluster.enumerate_cluster_nodes()
        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        # Label falls back to the timezone of the node doing the lookup
        self.assertEqual(node.timezone, settings.TIME_ZONE)
        # Measurement has no fallback: unknown is unknown
        self.assertIsNone(node.db_offset)
        self.assertEqual(node.mac, consts.NULL_MAC)

    def test_as_dict_contains_all_fields(self) -> None:
        models.Properties.objects.create(
            owner_id="cluster",
            owner_type="cluster",
            key="legacy.example.com|10.0.0.9",
            value={"last_seen": "2026-01-01T00:00:00+00:00"},
        )

        node = cluster.enumerate_cluster_nodes()[0]
        as_dict: dict[str, typing.Any] = node.as_dict()
        self.assertEqual(
            set(as_dict.keys()),
            {"hostname", "ip", "last_seen", "mac", "timezone", "db_offset"},
        )
        self.assertIsNone(as_dict["db_offset"])
