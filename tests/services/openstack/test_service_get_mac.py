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
Tests for OpenStackLiveService.get_mac unique_id resolution.

Covers the two address sources:
  * server 'addresses' (OpenStack-managed addressing / internal DHCP)
  * Neutron port fallback (external DHCP, where 'addresses' is empty)

and the resilience to a not-yet-created server (NotFoundError -> '').
"""

import copy
import typing

from unittest import mock

from uds.core import exceptions
from uds.services.OpenStack.openstack import client
from uds.services.OpenStack.openstack import types as openstack_types

from ...utils.test import UDSTransactionTestCase
from . import fixtures

# Undecorated mac-resolution logic (the @cached wrapper would need a real cache backend).
# Running it against the mocked api keeps these tests exercising the real behaviour while
# the service simply delegates to api.get_server_mac().
_real_get_server_mac = getattr(client.OpenStackClient.get_server_mac, "__wrapped__")


def _bind_real_get_server_mac(api: mock.MagicMock) -> None:
    api.get_server_mac.side_effect = lambda vmid, **kw: _real_get_server_mac(api, vmid, **kw)


def _server_with_addresses(
    addresses: list[openstack_types.ServerInfo.AddresInfo],
) -> openstack_types.ServerInfo:
    server = copy.deepcopy(fixtures.SERVERS_LIST[0])
    server.status = openstack_types.ServerStatus.ACTIVE  # not "lost", so validated() passes
    server.addresses = addresses
    return server


def _port(mac: str, *, device_id: str = "sid1", ip: str = "") -> openstack_types.PortInfo:
    return openstack_types.PortInfo(
        id="pid1",
        name="port1",
        status=openstack_types.PortStatus.ACTIVE,
        device_id=device_id,
        network_id="net1",
        mac_address=mac,
        fixed_ips=[ip] if ip else [],
    )


class TestOpenStackGetMac(UDSTransactionTestCase):
    def _service_with_api(self) -> tuple[typing.Any, mock.MagicMock]:
        with fixtures.patched_provider() as prov:
            service = fixtures.create_live_service(prov)
        api = mock.MagicMock()
        _bind_real_get_server_mac(api)
        service.cached_api = api  # property returns this without hitting the provider
        return service, api

    def test_get_mac_from_server_addresses(self) -> None:
        # Internal DHCP: server addresses carry the mac
        service, api = self._service_with_api()
        api.get_server_info.return_value = _server_with_addresses(
            [openstack_types.ServerInfo.AddresInfo(version=4, ip="10.0.0.5", mac="FA:16:3E:00:00:01", type="fixed")]
        )

        mac = service.get_mac(None, "sid1", for_unique_id=True)

        self.assertEqual(mac, "FA:16:3E:00:00:01")
        api.list_ports.assert_not_called()  # no fallback needed

    def test_get_mac_falls_back_to_neutron_port(self) -> None:
        # External DHCP: server addresses empty -> use the Neutron port mac
        service, api = self._service_with_api()
        api.get_server_info.return_value = _server_with_addresses([])
        api.list_ports.return_value = [_port("FA:16:3E:AB:CD:EF", device_id="sid1")]

        mac = service.get_mac(None, "sid1", for_unique_id=True)

        self.assertEqual(mac, "FA:16:3E:AB:CD:EF")
        api.list_ports.assert_called_once_with(device_id="sid1")

    def test_get_mac_raises_when_server_not_found(self) -> None:
        # Server not queryable yet: get_server_mac does not swallow it; the caller/state
        # checker handles the NotFoundError.
        service, api = self._service_with_api()
        api.get_server_info.side_effect = exceptions.services.generics.NotFoundError("Not found")

        with self.assertRaises(exceptions.services.generics.NotFoundError):
            service.get_mac(None, "sid1", for_unique_id=True)

        api.list_ports.assert_not_called()

    def test_get_mac_empty_when_no_addresses_and_no_ports(self) -> None:
        # No addresses and no ports yet -> '' so the state checker retries
        service, api = self._service_with_api()
        api.get_server_info.return_value = _server_with_addresses([])
        api.list_ports.return_value = []

        mac = service.get_mac(None, "sid1", for_unique_id=True)

        self.assertEqual(mac, "")

    def test_get_mac_empty_when_port_lookup_not_found(self) -> None:
        # addresses empty and Neutron returns 404 -> '' (no exception leaks to caller)
        service, api = self._service_with_api()
        api.get_server_info.return_value = _server_with_addresses([])
        api.list_ports.side_effect = exceptions.services.generics.NotFoundError("Not found")

        mac = service.get_mac(None, "sid1", for_unique_id=True)

        self.assertEqual(mac, "")

    def test_get_mac_empty_when_port_lookup_permission_denied(self) -> None:
        # Neutron policy forbids listing ports (403) -> '' instead of forcing ERROR.
        # A bare Error (the base class) covers 403/RBAC/availability/transient errors.
        service, api = self._service_with_api()
        api.get_server_info.return_value = _server_with_addresses([])
        api.list_ports.side_effect = exceptions.services.generics.Error("Forbidden")

        mac = service.get_mac(None, "sid1", for_unique_id=True)

        self.assertEqual(mac, "")


class TestPortInfo(UDSTransactionTestCase):
    def test_from_dict_uppercases_mac_and_extracts_ips(self) -> None:
        port = openstack_types.PortInfo.from_dict(
            {
                "id": "pid1",
                "name": "p1",
                "status": "ACTIVE",
                "device_id": "sid1",
                "network_id": "net1",
                "mac_address": "fa:16:3e:11:22:33",
                "fixed_ips": [{"ip_address": "10.0.0.9", "subnet_id": "sub1"}, {"subnet_id": "sub2"}],
            }
        )
        self.assertEqual(port.mac_address, "FA:16:3E:11:22:33")
        self.assertEqual(port.fixed_ips, ["10.0.0.9"])
        self.assertEqual(port.status, openstack_types.PortStatus.ACTIVE)

    def test_from_dict_tolerates_missing_fields(self) -> None:
        port = openstack_types.PortInfo.from_dict({"id": "pid9"})
        self.assertEqual(port.id, "pid9")
        self.assertEqual(port.name, "pid9")  # falls back to id
        self.assertEqual(port.mac_address, "")
        self.assertEqual(port.fixed_ips, [])
        self.assertEqual(port.device_id, "")
