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

Tests for the client/actor address checks (uds.checks.network).
"""

from django.utils import timezone

from uds import models
from uds.core import types
from uds.core.checks import runner as runner_module
from uds.core.util.config import GlobalConfig

from ..utils.test import UDSTransactionTestCase


def _log_login(user: str, ip: str) -> None:
    models.Log.objects.create(
        owner_id=1,
        owner_type=types.log.LogObjectType.AUTHENTICATOR,
        created=timezone.now(),
        source=types.log.LogSource.WEB,
        level=types.log.LogLevel.INFO,
        data=f"user {user} has Logged in from {ip} where os is Linux",
    )


def _log_actor_blocked(ip: str) -> None:
    models.Log.objects.create(
        owner_id=0,
        owner_type=types.log.LogObjectType.SYSLOG,
        created=timezone.now(),
        source=types.log.LogSource.LOGS,
        level=types.log.LogLevel.INFO,
        data=f"Access to actor from {ip} is blocked for 300 seconds since last fail",
    )


class NetworkChecksTest(UDSTransactionTestCase):
    def setUp(self) -> None:
        super().setUp()
        GlobalConfig.BEHIND_PROXY.set(False)

    def _run_check(self, check_id: str) -> types.checks.CheckResult:
        results = {result.id: result for result in runner_module.run_checks(types.checks.CheckKind.AUTOMATIC)}
        return results[check_id]

    def test_proxy_address_fails_when_everybody_comes_from_one_private_ip(self) -> None:
        for i in range(20):
            _log_login(f"user{i % 5}", "10.0.0.1")
        result = self._run_check("client-ip-is-proxy-address")
        self.assertFalse(result.ok, result.message)
        self.assertIn("10.0.0.1", result.message)

    def test_proxy_address_passes_with_distinct_client_ips(self) -> None:
        for i in range(20):
            _log_login(f"user{i % 5}", f"10.0.0.{i % 5 + 1}")
        self.assertTrue(self._run_check("client-ip-is-proxy-address").ok)

    def test_proxy_address_passes_with_public_ip(self) -> None:
        for i in range(20):
            _log_login(f"user{i % 5}", "80.58.61.250")
        self.assertTrue(self._run_check("client-ip-is-proxy-address").ok)

    def test_proxy_address_passes_with_few_logins(self) -> None:
        for i in range(5):
            _log_login(f"user{i}", "10.0.0.1")
        self.assertTrue(self._run_check("client-ip-is-proxy-address").ok)

    def test_proxy_address_passes_when_behind_proxy_is_enabled(self) -> None:
        GlobalConfig.BEHIND_PROXY.set(True)
        for i in range(20):
            _log_login(f"user{i % 5}", "10.0.0.1")
        self.assertTrue(self._run_check("client-ip-is-proxy-address").ok)

    def test_actor_blocks_pass_without_blocks(self) -> None:
        self.assertTrue(self._run_check("actor-ips-blocked-24h").ok)

    def test_actor_blocks_list_blocked_addresses(self) -> None:
        _log_actor_blocked("192.168.1.50")
        _log_actor_blocked("192.168.1.50")
        _log_actor_blocked("192.168.1.51")
        result = self._run_check("actor-ips-blocked-24h")
        self.assertFalse(result.ok, result.message)
        self.assertIn("2 address(es)", result.message)
        self.assertIn("192.168.1.50", result.message)
