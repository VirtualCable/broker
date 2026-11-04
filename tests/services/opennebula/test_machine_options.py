"""
Author: Andres Schumann, aschumann at virtualcable dot es
"""

import typing
from unittest import mock

from defusedxml import ElementTree

from uds.core import types
from uds.services.OpenNebula import on

from ...utils.test import UDSTransactionTestCase
from . import fixtures

TEMPLATE_XML: typing.Final[str] = """
<VMTEMPLATE>
  <ID>10</ID>
  <NAME>base</NAME>
  <TEMPLATE>
    <CPU><![CDATA[0.1]]></CPU>
    <MEMORY><![CDATA[128]]></MEMORY>
    <VCPU><![CDATA[1]]></VCPU>
    <NIC>
      <IP><![CDATA[172.16.100.5]]></IP>
      <MODEL><![CDATA[virtio]]></MODEL>
      <NETWORK><![CDATA[vnet]]></NETWORK>
      <NETWORK_UNAME><![CDATA[oneadmin]]></NETWORK_UNAME>
    </NIC>
    <NIC>
      <NETWORK><![CDATA[management]]></NETWORK>
    </NIC>
  </TEMPLATE>
</VMTEMPLATE>
"""

NO_NIC_TEMPLATE_XML: typing.Final[str] = """
<VMTEMPLATE>
  <ID>10</ID>
  <TEMPLATE>
    <CPU><![CDATA[0.1]]></CPU>
    <MEMORY><![CDATA[128]]></MEMORY>
  </TEMPLATE>
</VMTEMPLATE>
"""


def _parsed(extra: str) -> dict[str, list[dict[str, str]] | str]:
    """Attribute name -> text, with NIC as a list of its own attributes"""
    result: dict[str, list[dict[str, str]] | str] = {}
    nics: list[dict[str, str]] = []
    for node in ElementTree.fromstring(extra):
        if node.tag == "NIC":
            nics.append({attr.tag: attr.text or "" for attr in node})
        else:
            result[node.tag] = node.text or ""
    if nics:
        result["NIC"] = nics
    return result


class TestMachineTemplate(UDSTransactionTestCase):
    def test_no_overrides_merge_nothing(self) -> None:
        self.assertEqual(on.template.machine_template(on.types.MachineOptions(), TEMPLATE_XML), "")

    def test_cores_and_memory(self) -> None:
        extra = _parsed(
            on.template.machine_template(on.types.MachineOptions(cores=4, memory_mb=2048), TEMPLATE_XML)
        )

        self.assertEqual(extra, {"VCPU": "4", "MEMORY": "2048"})

    def test_only_the_set_values_are_overridden(self) -> None:
        extra = _parsed(on.template.machine_template(on.types.MachineOptions(memory_mb=512), TEMPLATE_XML))

        self.assertEqual(extra, {"MEMORY": "512"})

    def test_network_replaces_only_the_first_interface(self) -> None:
        extra = _parsed(on.template.machine_template(on.types.MachineOptions(network="7"), TEMPLATE_XML))

        # OpenNebula replaces every NIC of the template with the ones sent, so the second one travels too
        self.assertEqual(
            extra,
            {
                "NIC": [
                    # Kept what is not tied to the previous network, dropped its name, owner and address
                    {"NETWORK_ID": "7", "MODEL": "virtio"},
                    {"NETWORK": "management"},
                ]
            },
        )

    def test_network_on_a_template_without_interfaces_adds_one(self) -> None:
        extra = _parsed(on.template.machine_template(on.types.MachineOptions(network="7"), NO_NIC_TEMPLATE_XML))

        self.assertEqual(extra, {"NIC": [{"NETWORK_ID": "7"}]})

    def test_values_are_escaped(self) -> None:
        template_xml = (
            "<VMTEMPLATE><TEMPLATE>"
            "<NIC><MODEL><![CDATA[a<b&c]]></MODEL><NETWORK><![CDATA[x]]></NETWORK></NIC>"
            "</TEMPLATE></VMTEMPLATE>"
        )

        extra = _parsed(on.template.machine_template(on.types.MachineOptions(network="3"), template_xml))

        self.assertEqual(extra, {"NIC": [{"NETWORK_ID": "3", "MODEL": "a<b&c"}]})


class TestEnumNetworks(UDSTransactionTestCase):
    def _networks(self, pool_xml: str) -> list[on.types.NetworkType]:
        api = on.client.OpenNebulaClient("user", "password", "http://localhost:2633/RPC2")
        with mock.patch.object(api, "connection") as connection:
            connection.one.vnpool.info.return_value = [True, pool_xml, 0]
            return list(api.enum_networks())

    def test_server_without_networks(self) -> None:
        self.assertEqual(self._networks("<VNET_POOL></VNET_POOL>"), [])

    def test_one_network(self) -> None:
        self.assertEqual(
            self._networks("<VNET_POOL><VNET><ID>0</ID><NAME>vnet</NAME></VNET></VNET_POOL>"),
            [on.types.NetworkType("0", "vnet")],
        )

    def test_several_networks(self) -> None:
        self.assertEqual(
            self._networks(
                "<VNET_POOL>"
                "<VNET><ID>0</ID><NAME>vnet</NAME></VNET>"
                "<VNET><ID>3</ID><NAME>dmz</NAME></VNET>"
                "</VNET_POOL>"
            ),
            [on.types.NetworkType("0", "vnet"), on.types.NetworkType("3", "dmz")],
        )


class TestDeployWithMachineOptions(UDSTransactionTestCase):
    def test_deploy_without_overrides_does_not_read_the_template(self) -> None:
        api = mock.Mock()
        api.instantiate_template.return_value = "55"

        vmid = on.template.deploy_from(api, "10", "vm-name", on.types.MachineOptions())

        self.assertEqual(vmid, "55")
        api.template_info.assert_not_called()
        api.instantiate_template.assert_called_once_with("10", "vm-name", False, "", False)

    def test_deploy_merges_the_overrides(self) -> None:
        api = mock.Mock()
        api.template_info.return_value = on.types.TemplateType("10", "base", 128, TEMPLATE_XML)
        api.instantiate_template.return_value = "55"

        on.template.deploy_from(api, "10", "vm-name", on.types.MachineOptions(cores=2, network="7"))

        args = api.instantiate_template.call_args.args
        self.assertEqual(args[:3], ("10", "vm-name", False))
        self.assertEqual(
            _parsed(args[3]),
            {"VCPU": "2", "NIC": [{"NETWORK_ID": "7", "MODEL": "virtio"}, {"NETWORK": "management"}]},
        )


class TestServiceMachineOptions(UDSTransactionTestCase):
    def test_options_default_to_keep_the_template(self) -> None:
        srvc = fixtures.create_service(fixtures.create_provider())

        self.assertEqual(srvc.machine_options(), on.types.MachineOptions())

    def test_options_come_from_the_machine_tab(self) -> None:
        srvc = fixtures.create_service(fixtures.create_provider(), cores=2, memory=1024, network="7")

        self.assertEqual(srvc.machine_options(), on.types.MachineOptions(cores=2, memory_mb=1024, network="7"))

    def test_fields_are_in_the_machine_tab(self) -> None:
        srvc = fixtures.create_service(fixtures.create_provider())

        for field in (srvc.cores, srvc.memory, srvc.network):
            self.assertEqual(field._field_info.tab, types.ui.Tab.MACHINE)

    def test_gui_offers_the_networks_of_the_server(self) -> None:
        provider = fixtures.create_provider()
        srvc = fixtures.create_service(provider)

        with (
            mock.patch.object(provider, "get_templates", return_value=[]),
            mock.patch.object(provider, "get_datastores", return_value=[]),
            mock.patch.object(
                provider,
                "get_networks",
                return_value=[on.types.NetworkType("0", "vnet"), on.types.NetworkType("3", "dmz")],
            ),
        ):
            srvc.init_gui()

        choices = typing.cast("list[types.ui.ChoiceItem]", srvc.network._field_info.choices)
        self.assertEqual(
            [(c.id, c.text) for c in choices],
            [("", "Same as template"), ("0", "vnet"), ("3", "dmz")],
        )

    def test_deployment_uses_the_options_of_the_service(self) -> None:
        with fixtures.patched_opennebula() as one:
            srvc = fixtures.create_service(fixtures.create_provider(), cores=2, memory=1024, network="7")

            vmid = srvc.deploy_from_template("vm-name", "10")

            self.assertEqual(
                one.deploy_options[vmid], on.types.MachineOptions(cores=2, memory_mb=1024, network="7")
            )
