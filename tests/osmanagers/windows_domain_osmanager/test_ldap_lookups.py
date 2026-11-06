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
"""

import typing

import ldap3

from tests.utils.test import UDSTestCase
from uds.core.environment import Environment
from uds.osmanagers.WindowsOsManager import windows_domain as osmanager

MACHINE_DN: typing.Final[str] = "CN=UDSPQV001,OU=VDI,DC=mvdi,DC=local"
GROUP_DN: typing.Final[str] = "CN=VDI Machines,OU=VDI,DC=mvdi,DC=local"


def ad_connection() -> typing.Any:
    # Real connections load the schema (get_info=ALL), so ldap3 rejects attribute names AD does not know
    server = ldap3.Server("fake-ad", get_info=typing.cast(typing.Any, ldap3.OFFLINE_AD_2012_R2))
    connection: typing.Any = ldap3.Connection(server, client_strategy=ldap3.MOCK_SYNC)
    connection.strategy.add_entry(MACHINE_DN, {"objectClass": ["computer"], "sAMAccountName": "UDSPQV001$"})
    connection.strategy.add_entry(
        GROUP_DN, {"objectClass": ["group"], "cn": "VDI Machines", "sAMAccountName": "VDI Machines"}
    )
    connection.bind()
    return connection


class WindowsDomainLdapLookupsTest(UDSTestCase):
    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.osmanager = osmanager.WinDomainOsManager(environment=Environment.testing_environment())
        self.osmanager.domain.value = "mvdi.local"
        self.osmanager.grp.value = "VDI Machines"

    def test_get_machine_returns_dn_with_ad_schema(self) -> None:
        self.assertEqual(self.osmanager._get_machine(ad_connection(), "UDSPQV001"), MACHINE_DN)

    def test_get_machine_returns_none_when_missing(self) -> None:
        self.assertIsNone(self.osmanager._get_machine(ad_connection(), "UNKNOWN"))

    def test_get_group_returns_dn_with_ad_schema(self) -> None:
        self.assertEqual(self.osmanager._get_group(ad_connection()), GROUP_DN)
