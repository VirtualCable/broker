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

import re
import typing
from unittest import mock

from tests.utils.test import UDSTestCase
from uds.core.environment import Environment
from uds.core.util import ldaputil
from uds.osmanagers.WindowsOsManager import windows_domain as osmanager

MACHINE_DN: typing.Final[str] = "CN=UDSPQV001,OU=VDI,DC=mvdi,DC=local"
GROUP_DN: typing.Final[str] = "CN=VDI Machines,OU=VDI,DC=mvdi,DC=local"


def ad_connection() -> typing.Any:
    """Fake native connection serving the canned AD entries."""
    connection = mock.MagicMock(spec=ldaputil.NativeConnection)
    entries: typing.Final[list[dict[str, list[str]]]] = [
        {"dn": MACHINE_DN, "sAMAccountName": ["UDSPQV001$"], "objectClass": ["computer"]},
        {
            "dn": GROUP_DN,
            "cn": ["VDI Machines"],
            "sAMAccountName": ["VDI Machines"],
            "objectClass": ["group"],
        },
    ]

    def search(
        base: str,
        ldap_filter: str,
        *,
        scope: int = 2,
        attributes: list[str] | None = None,
        size_limit: int = 100,
    ) -> dict[str, typing.Any]:
        del base, scope, attributes, size_limit
        object_class = "computer" if "objectClass=computer" in ldap_filter else "group"
        # First sAMAccountName value present in the filter, if any
        sam_match = re.search(r"sAMAccountName=([^)]+)\)", ldap_filter)
        sam_value = sam_match.group(1) if sam_match else None
        matched: list[dict[str, typing.Any]] = []
        for entry in entries:
            if entry["objectClass"][0] != object_class:
                continue
            if sam_value is not None and sam_value not in (
                entry["sAMAccountName"][0],
                entry.get("cn", [""])[0],
            ):
                continue
            matched.append(dict(entry))
        return {"entries": matched, "referrals": []}

    connection.search.side_effect = search
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
