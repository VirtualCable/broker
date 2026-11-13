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
Author: Andres Schumann, aschumann at virtualcable dot es
"""

import typing

from unittest import mock

from uds.services.Proxmox.proxmox import client as prox_client
from uds.services.Proxmox.proxmox import types as prox_types

from ...utils.test import UDSTestCase
from . import fixtures

NET_CONFIG = "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0,firewall=1"


class TestNetworkConfigurationBridge(UDSTestCase):
    def test_set_bridge_replaces_the_existing_bridge_and_keeps_the_rest(self) -> None:
        net = prox_types.NetworkConfiguration.from_str("net0", NET_CONFIG)
        net.set_bridge("vmbr1")
        self.assertEqual(net.netdata, "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr1,firewall=1")

    def test_set_bridge_appends_when_there_is_no_bridge(self) -> None:
        net = prox_types.NetworkConfiguration.from_str("net0", "virtio=AA:BB:CC:DD:EE:FF")
        net.set_bridge("vmbr1")
        self.assertEqual(net.netdata, "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr1")


class TestProxmoxSetVmNetMacOptions(UDSTestCase):
    def setUp(self) -> None:
        self.pclient = prox_client.ProxmoxClient("host", 8006, "user", "password")
        cfg = mock.Mock()
        cfg.networks = [prox_types.NetworkConfiguration.from_str("net0", NET_CONFIG)]
        self.get_vm_config = mock.patch.object(self.pclient, "get_vm_config", return_value=cfg).start()
        mock.patch.object(self.pclient, "get_vm_info", return_value=mock.Mock(node="node1")).start()
        self.do_post: mock.Mock = mock.patch.object(self.pclient, "do_post").start()
        self.addCleanup(mock.patch.stopall)

    def test_mac_only_keeps_template_hardware_and_bridge(self) -> None:
        self.pclient.set_vm_net_mac(100, "00:11:22:33:44:55")
        self.do_post.assert_called_once_with(
            "nodes/node1/qemu/100/config",
            data=[("net0", "virtio=00:11:22:33:44:55,bridge=vmbr0,firewall=1")],
            node="node1",
        )

    def test_cores_memory_and_bridge_are_written_with_the_mac(self) -> None:
        self.pclient.set_vm_net_mac(100, "00:11:22:33:44:55", cores=4, memory=4096, bridge="vmbr1")
        self.do_post.assert_called_once_with(
            "nodes/node1/qemu/100/config",
            data=[
                ("net0", "virtio=00:11:22:33:44:55,bridge=vmbr1,firewall=1"),
                ("cores", "4"),
                ("memory", "4096"),
            ],
            node="node1",
        )

    def test_zero_cores_and_memory_are_not_written(self) -> None:
        self.pclient.set_vm_net_mac(100, "00:11:22:33:44:55", cores=0, memory=0, bridge="vmbr1")
        data = self.do_post.call_args.kwargs["data"]
        self.assertEqual([key for key, _ in data], ["net0"])

    def test_config_is_read_fresh_before_writing(self) -> None:
        self.pclient.set_vm_net_mac(100, "00:11:22:33:44:55")
        self.get_vm_config.assert_called_once_with(100, "node1", force=True)


class TestProxmoxServiceMachineOptions(UDSTestCase):
    def test_set_vm_options_passes_the_configured_options(self) -> None:
        with fixtures.patched_provider() as provider:
            srvc = fixtures.create_service_linked(provider=provider, cores="2", memory="2048", network="vmbr1")
            srvc.set_vm_options(100, "00:11:22:33:44:55")
            typing.cast(mock.Mock, provider.api).set_vm_net_mac.assert_called_once_with(
                100, "00:11:22:33:44:55", cores=2, memory=2048, bridge="vmbr1"
            )

    def test_set_vm_options_empty_network_keeps_template_bridge(self) -> None:
        with fixtures.patched_provider() as provider:
            srvc = fixtures.create_service_linked(provider=provider, cores="0", memory="0", network="")
            srvc.set_vm_options(100, "00:11:22:33:44:55")
            typing.cast(mock.Mock, provider.api).set_vm_net_mac.assert_called_once_with(
                100, "00:11:22:33:44:55", cores=0, memory=0, bridge=None
            )


class TestProxmoxLinkedDeploymentOptionsOrder(UDSTestCase):
    def test_options_are_written_before_ha_can_start_the_vm(self) -> None:
        with fixtures.patched_provider() as provider:
            srvc = fixtures.create_service_linked(provider=provider, cores="2", memory="2048", network="vmbr1")
            userservice = fixtures.create_userservice_linked(service=srvc)
            userservice._vmid = "100"
            api = typing.cast(mock.Mock, provider.api)
            calls = mock.Mock()
            calls.attach_mock(api.set_vm_net_mac, "set_vm_net_mac")
            calls.attach_mock(api.enable_vm_ha, "enable_vm_ha")
            with mock.patch.object(userservice, "get_unique_id", return_value="00:11:22:33:44:55"):
                userservice.op_create_completed()
            self.assertEqual([name for name, *_ in calls.mock_calls], ["set_vm_net_mac", "enable_vm_ha"])
