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
Tests for OpenStackServiceFixed.get_mac unique_id resolution.

Mirrors the live-service coverage: server 'addresses' (internal DHCP), Neutron port
fallback (external DHCP), and resilience to not-found / permission errors -> ''.
"""
import copy
import typing
from unittest import mock

from uds.core import exceptions

from . import fixtures

from ...utils.test import UDSTransactionTestCase

from uds.services.OpenStack.openstack import client
from uds.services.OpenStack.openstack import types as openstack_types

# Undecorated mac-resolution logic (the @cached wrapper would need a real cache backend).
# Running it against the mocked api keeps these tests exercising the real behaviour while
# the service simply delegates to api.get_server_mac().
_real_get_server_mac = getattr(client.OpenStackClient.get_server_mac, '__wrapped__')


def _bind_real_get_server_mac(api: mock.MagicMock) -> None:
    api.get_server_mac.side_effect = lambda vmid, **kw: _real_get_server_mac(api, vmid, **kw)


def _server_with_addresses(
    addresses: list[openstack_types.ServerInfo.AddresInfo],
) -> openstack_types.ServerInfo:
    server = copy.deepcopy(fixtures.SERVERS_LIST[0])
    server.status = openstack_types.ServerStatus.ACTIVE
    server.addresses = addresses
    return server


def _port(mac: str, *, device_id: str = 'sid1', ip: str = '') -> openstack_types.PortInfo:
    return openstack_types.PortInfo(
        id='pid1',
        name='port1',
        status=openstack_types.PortStatus.ACTIVE,
        device_id=device_id,
        network_id='net1',
        mac_address=mac,
        fixed_ips=[ip] if ip else [],
    )


class TestOpenStackFixedGetMac(UDSTransactionTestCase):
    def _service_with_api(self) -> tuple[typing.Any, mock.MagicMock]:
        with fixtures.patched_provider() as prov:
            service = fixtures.create_fixed_service(prov)
        api = mock.MagicMock()
        _bind_real_get_server_mac(api)
        service._api = api  # the api property returns the cached client without hitting the provider
        return service, api

    def test_get_mac_from_server_addresses(self) -> None:
        # Internal DHCP: server addresses carry the mac
        service, api = self._service_with_api()
        api.get_server_info.return_value = _server_with_addresses(
            [openstack_types.ServerInfo.AddresInfo(version=4, ip='10.0.0.5', mac='FA:16:3E:00:00:01', type='fixed')]
        )

        mac = service.get_mac('sid1')

        self.assertEqual(mac, 'FA:16:3E:00:00:01')
        api.list_ports.assert_not_called()

    def test_get_mac_falls_back_to_neutron_port(self) -> None:
        # External DHCP: server addresses empty -> use the Neutron port mac
        service, api = self._service_with_api()
        api.get_server_info.return_value = _server_with_addresses([])
        api.list_ports.return_value = [_port('FA:16:3E:AB:CD:EF', device_id='sid1')]

        mac = service.get_mac('sid1')

        self.assertEqual(mac, 'FA:16:3E:AB:CD:EF')
        api.list_ports.assert_called_once_with(device_id='sid1')

    def test_get_mac_raises_when_server_not_found(self) -> None:
        # Server missing: get_server_mac does not swallow it; the caller handles NotFoundError.
        service, api = self._service_with_api()
        api.get_server_info.side_effect = exceptions.services.generics.NotFoundError('Not found')

        with self.assertRaises(exceptions.services.generics.NotFoundError):
            service.get_mac('sid1')

        api.list_ports.assert_not_called()

    def test_get_mac_empty_when_no_addresses_and_no_ports(self) -> None:
        # External DHCP but the port is not there yet -> '' instead of IndexError
        service, api = self._service_with_api()
        api.get_server_info.return_value = _server_with_addresses([])
        api.list_ports.return_value = []

        mac = service.get_mac('sid1')

        self.assertEqual(mac, '')

    def test_get_mac_empty_when_port_lookup_permission_denied(self) -> None:
        # Neutron policy forbids listing ports (403) -> '' instead of crashing
        service, api = self._service_with_api()
        api.get_server_info.return_value = _server_with_addresses([])
        api.list_ports.side_effect = exceptions.services.generics.Error('Forbidden')

        mac = service.get_mac('sid1')

        self.assertEqual(mac, '')
