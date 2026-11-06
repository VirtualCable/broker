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
# AND EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
# IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
# OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
Provider types are clustered in the administration "New" menu by ``group``.

Author: Andres Schumann, aschumann at virtualcable dot es
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import typing
from unittest import mock

from uds.core import services, types
from uds.services.OVirt.provider import OVirtProvider
from uds.services.OpenGnsys.provider import OGProvider
from uds.services.OpenNebula.provider import OpenNebulaProvider
from uds.services.OpenShift.provider import OpenshiftProvider
from uds.services.OpenStack.provider import OpenStackProvider
from uds.services.OpenStack.provider_legacy import OpenStackProviderLegacy
from uds.services.PhysicalMachines.provider import PhysicalMachinesProvider
from uds.services.Proxmox.provider import ProxmoxProvider
from uds.services.Xen.provider import XenProvider

from ....utils import rest

Grouping = types.services.ProviderGrouping

OPEN_PROVIDER_GROUPS: typing.Final[dict[type[services.ServiceProvider], Grouping]] = {
    ProxmoxProvider: Grouping.HYPERVISORS,
    XenProvider: Grouping.HYPERVISORS,
    OVirtProvider: Grouping.HYPERVISORS,
    OpenStackProvider: Grouping.CLOUD_PROVIDERS,
    OpenStackProviderLegacy: Grouping.CLOUD_PROVIDERS,
    OpenNebulaProvider: Grouping.CLOUD_PROVIDERS,
    OpenshiftProvider: Grouping.CLOUD_PROVIDERS,
    PhysicalMachinesProvider: Grouping.EXISTING_MACHINES,
    OGProvider: Grouping.EXISTING_MACHINES,
}


class ProviderGroupingTest(rest.test.RESTTestCase):
    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def test_base_provider_defaults_to_other(self) -> None:
        self.assertEqual(services.ServiceProvider.group, Grouping.OTHER)

    def test_open_providers_have_their_group(self) -> None:
        for provider_type, expected in OPEN_PROVIDER_GROUPS.items():
            with self.subTest(provider=provider_type.__name__):
                self.assertEqual(provider_type.group, expected)

    def test_every_registered_provider_has_a_valid_group(self) -> None:
        for provider_type in services.factory().providers().values():
            with self.subTest(provider=provider_type.__name__):
                self.assertIsInstance(provider_type.group, Grouping)

    def test_rest_types_expose_group_of_every_provider(self) -> None:
        response = self.client.rest_get("providers/types")
        self.assertEqual(response.status_code, 200)
        exposed = {item["type"]: item["group"] for item in response.json()}
        for provider_type in services.factory().providers().values():
            with self.subTest(provider=provider_type.__name__):
                self.assertEqual(exposed[provider_type.mod_type()], provider_type.group.value)

    def test_rest_types_come_in_the_order_of_their_groups(self) -> None:
        # The administration builds its "New" menu in the order the types arrive
        group_order = list(Grouping)
        providers = {
            provider_type.mod_type(): provider_type for provider_type in services.factory().providers().values()
        }

        response = self.client.rest_get("providers/types")
        self.assertEqual(response.status_code, 200)
        positions = [group_order.index(providers[item["type"]].group) for item in response.json()]

        self.assertEqual(positions, sorted(positions))
        self.assertGreater(len(set(positions)), 1)

    def test_rest_types_translate_the_group(self) -> None:
        def translated(text: str) -> str:
            return f"translated:{text}"

        with mock.patch("uds.REST.model.base._", side_effect=translated):
            response = self.client.rest_get("providers/types")
        self.assertEqual(response.status_code, 200)
        exposed = {item["type"]: item["group"] for item in response.json()}
        self.assertEqual(exposed[ProxmoxProvider.mod_type()], f"translated:{Grouping.HYPERVISORS.value}")
