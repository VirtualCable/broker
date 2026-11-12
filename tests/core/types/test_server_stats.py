#
# Copyright (c) 2026 Virtual Cable S.L.U.
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
#    * Neither the name of Virtual Cable S.L.U. nor the names of its contributors
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
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

from unittest import mock

from uds.core import types
from uds.core.managers.servers_api.requester import ServerApiRequester

from ...fixtures import servers as fixtures_servers
from ...utils.test import UDSTestCase


class ServerStatsFromDictTest(UDSTestCase):
    def test_keeps_received_stamp(self) -> None:
        stamp = fixtures_servers.outdated_stats_stamp()

        stats = types.servers.ServerStats.from_dict({"memtotal": 1024, "stamp": stamp})

        self.assertEqual(stats.stamp, stamp)
        self.assertFalse(stats.is_valid)

    def test_stamps_now_when_none_received(self) -> None:
        stats = types.servers.ServerStats.from_dict({"memtotal": 1024})

        self.assertTrue(stats.is_valid)


class ServerStatsStorageTest(UDSTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.server = fixtures_servers.create_server()

    def test_read_keeps_stored_stamp(self) -> None:
        stamp = fixtures_servers.outdated_stats_stamp()
        fixtures_servers.store_stats(self.server, stamp)

        stats = self.server.stats

        assert stats is not None
        self.assertEqual(stats.stamp, stamp)
        self.assertFalse(stats.is_valid)

    def test_write_stamps_now(self) -> None:
        self.server.stats = types.servers.ServerStats(memtotal=1024, stamp=fixtures_servers.outdated_stats_stamp())

        stats = self.server.stats

        assert stats is not None
        self.assertTrue(stats.is_valid)

    def test_requester_refreshes_outdated_stats(self) -> None:
        fixtures_servers.store_stats(self.server, fixtures_servers.outdated_stats_stamp())
        requester = ServerApiRequester(self.server)

        with mock.patch.object(requester, "get", return_value={"memtotal": 2048, "current_users": 3}) as get:
            stats = requester.get_stats(enable_refresh=True)

        get.assert_called_once_with("stats")
        assert stats is not None
        self.assertEqual(stats.current_users, 3)
        self.assertTrue(stats.is_valid)

    def test_requester_reuses_valid_stats(self) -> None:
        self.server.stats = types.servers.ServerStats(memtotal=1024, current_users=1)
        requester = ServerApiRequester(self.server)

        with mock.patch.object(requester, "get") as get:
            stats = requester.get_stats(enable_refresh=True)

        get.assert_not_called()
        assert stats is not None
        self.assertEqual(stats.current_users, 1)


class ServerStatsLoadTest(UDSTestCase):
    @staticmethod
    def _stats(*, current_users: int) -> types.servers.ServerStats:
        return types.servers.ServerStats(
            memused=1024 * 1024 * 1024,
            memtotal=4 * 1024 * 1024 * 1024,
            cpuused=0.2,
            current_users=current_users,
        )

    def test_one_user_gives_a_normal_load(self) -> None:
        load = self._stats(current_users=1).load()

        self.assertLessEqual(load, 1.0)

    def test_load_is_unchanged_below_max_expected_users(self) -> None:
        weights = types.servers.ServerStatsWeights()
        load_one = self._stats(current_users=1).load(weights=weights)
        load_two = self._stats(current_users=2).load(weights=weights)

        self.assertLess(load_one, load_two)
        self.assertLessEqual(load_two, 1.0)

    def test_reaching_max_expected_users_moves_server_to_the_end(self) -> None:
        weights = types.servers.ServerStatsWeights(max_expected_users=100)

        load = self._stats(current_users=100).load(weights=weights)

        self.assertGreaterEqual(load, 1_000_000)

    def test_users_weight_zero_ignores_user_count(self) -> None:
        weights = types.servers.ServerStatsWeights(users=0, max_expected_users=100)

        load = self._stats(current_users=500).load(weights=weights)

        self.assertLessEqual(load, 1.0)
