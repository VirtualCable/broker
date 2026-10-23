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

import copy
import typing
from unittest import mock

from uds.core import exceptions
from uds.services.OpenShift import helpers
from uds.services.OpenShift.openshift import client as oshift_client
from uds.services.OpenShift.openshift import exceptions as oshift_exceptions
from uds.services.OpenShift.openshift import types as oshift_types

from tests.utils.test import UDSTransactionTestCase

from . import fixtures

TEMPLATE_VM: dict[str, typing.Any] = {
    "apiVersion": "kubevirt.io/v1",
    "kind": "VirtualMachine",
    "metadata": {"name": "tpl", "namespace": "ns", "uid": "u", "resourceVersion": "1"},
    "spec": {
        "runStrategy": "Halted",
        "template": {
            "spec": {
                "domain": {
                    "cpu": {"cores": 2},
                    "memory": {"guest": "4Gi"},
                    "resources": {"requests": {"memory": "4Gi"}},
                    "devices": {
                        "interfaces": [{"name": "default", "masquerade": {}, "macAddress": "02:00:00:00:00:01"}],
                    },
                },
                "networks": [{"name": "default", "pod": {}}],
                "volumes": [{"name": "rootdisk", "dataVolume": {"name": "tpl-disk"}}],
            }
        },
    },
}

SOURCE_PVC: dict[str, typing.Any] = {
    "spec": {
        "storageClassName": "gp3-csi",
        "volumeMode": "Block",
        "resources": {"requests": {"storage": "30Gi"}},
    }
}


def _client() -> oshift_client.OpenshiftClient:
    return oshift_client.OpenshiftClient(
        cluster_url="https://oauth.example",
        api_url="https://api.example:6443",
        username="user",
        password="pass",
        namespace="ns",
    )


class TestOpenshiftCreateVmWithMachineOptions(UDSTransactionTestCase):
    def _create(self, options: oshift_types.MachineOptions | None) -> dict[str, typing.Any]:
        client = _client()

        def fake_do_request(method: str, path: str, **kwargs: typing.Any) -> typing.Any:
            if method == "GET" and "/virtualmachines/" in path:
                return copy.deepcopy(TEMPLATE_VM)
            if method == "GET" and "/persistentvolumeclaims/" in path:
                return copy.deepcopy(SOURCE_PVC)
            if method == "GET":
                raise oshift_exceptions.OpenshiftNotFoundError("not found")
            return {}

        with mock.patch.object(client, "do_request", side_effect=fake_do_request) as do_request:
            if options is None:
                client.create_vm_from_pvc("tpl", "new", "new-disk", "tpl-disk")
            else:
                client.create_vm_from_pvc("tpl", "new", "new-disk", "tpl-disk", options=options)
        post = next(c for c in do_request.call_args_list if c.args[0] == "POST")
        return post.kwargs["data"]

    def test_without_options_keeps_template_values(self) -> None:
        body = self._create(None)
        domain = body["spec"]["template"]["spec"]["domain"]
        self.assertEqual(domain["cpu"], {"cores": 2})
        self.assertEqual(domain["memory"], {"guest": "4Gi"})
        self.assertNotIn("instancetype", body["spec"])
        self.assertEqual(body["spec"]["template"]["spec"]["networks"], [{"name": "default", "pod": {}}])
        self.assertEqual(body["spec"]["dataVolumeTemplates"][0]["spec"]["pvc"]["storageClassName"], "gp3-csi")
        self.assertEqual(body["spec"]["dataVolumeTemplates"][0]["spec"]["pvc"]["volumeMode"], "Block")

    def test_default_options_keep_template_values(self) -> None:
        self.assertEqual(self._create(oshift_types.MachineOptions()), self._create(None))

    def test_storage_class_override_drops_source_volume_mode(self) -> None:
        body = self._create(oshift_types.MachineOptions(storage_class="gp2-csi"))
        dv_spec = body["spec"]["dataVolumeTemplates"][0]["spec"]
        # The "storage" API lets CDI take the volumeMode from the StorageProfile of the new class
        self.assertNotIn("pvc", dv_spec)
        self.assertEqual(dv_spec["storage"]["storageClassName"], "gp2-csi")
        self.assertEqual(dv_spec["storage"]["resources"], {"requests": {"storage": "30Gi"}})
        self.assertNotIn("volumeMode", dv_spec["storage"])

    def test_cores_and_memory_override(self) -> None:
        body = self._create(oshift_types.MachineOptions(cores=4, memory_mb=8192))
        domain = body["spec"]["template"]["spec"]["domain"]
        self.assertEqual(domain["cpu"]["cores"], 4)
        self.assertEqual(domain["memory"]["guest"], "8192Mi")
        self.assertEqual(domain["resources"]["requests"]["memory"], "8192Mi")

    def test_only_cores_keeps_template_memory(self) -> None:
        body = self._create(oshift_types.MachineOptions(cores=4))
        domain = body["spec"]["template"]["spec"]["domain"]
        self.assertEqual(domain["cpu"]["cores"], 4)
        self.assertEqual(domain["memory"], {"guest": "4Gi"})

    def test_instance_type_replaces_cpu_and_memory(self) -> None:
        body = self._create(oshift_types.MachineOptions(instance_type="u1.large"))
        self.assertEqual(
            body["spec"]["instancetype"],
            {"kind": "VirtualMachineClusterInstancetype", "name": "u1.large"},
        )
        domain = body["spec"]["template"]["spec"]["domain"]
        # KubeVirt rejects a VM that sets cpu/memory guest together with an instance type
        self.assertNotIn("cpu", domain)
        self.assertNotIn("memory", domain)
        self.assertNotIn("memory", domain.get("resources", {}).get("requests", {}))

    def test_cores_on_template_with_instance_type_fails_clearly(self) -> None:
        template = copy.deepcopy(TEMPLATE_VM)
        template["spec"]["instancetype"] = {"kind": "VirtualMachineClusterInstancetype", "name": "u1.small"}
        with self.assertRaises(oshift_exceptions.OpenshiftError):
            oshift_types.MachineOptions(cores=4).apply_to_vm(template)

    def test_instance_type_on_template_with_instance_type_replaces_it(self) -> None:
        template = copy.deepcopy(TEMPLATE_VM)
        template["spec"]["instancetype"] = {"kind": "VirtualMachineInstancetype", "name": "x", "revisionName": "r"}
        oshift_types.MachineOptions(instance_type="u1.large").apply_to_vm(template)
        self.assertEqual(
            template["spec"]["instancetype"], {"kind": "VirtualMachineClusterInstancetype", "name": "u1.large"}
        )

    def test_network_override_uses_multus_on_first_interface(self) -> None:
        body = self._create(oshift_types.MachineOptions(network="ns/vlan10"))
        spec = body["spec"]["template"]["spec"]
        self.assertEqual(spec["networks"], [{"name": "default", "multus": {"networkName": "ns/vlan10"}}])
        iface = spec["domain"]["devices"]["interfaces"][0]
        self.assertIn("bridge", iface)
        self.assertNotIn("masquerade", iface)
        self.assertNotIn("macAddress", iface)

    def test_pod_network_override_on_multus_template(self) -> None:
        template = copy.deepcopy(TEMPLATE_VM)
        template["spec"]["template"]["spec"]["networks"] = [{"name": "default", "multus": {"networkName": "ns/x"}}]
        template["spec"]["template"]["spec"]["domain"]["devices"]["interfaces"] = [{"name": "default", "bridge": {}}]
        oshift_types.MachineOptions(network=oshift_types.POD_NETWORK).apply_to_vm(template)
        spec = template["spec"]["template"]["spec"]
        self.assertEqual(spec["networks"], [{"name": "default", "pod": {}}])
        self.assertEqual(spec["domain"]["devices"]["interfaces"][0], {"name": "default", "masquerade": {}})


class TestOpenshiftClientListings(UDSTransactionTestCase):
    def test_list_storage_classes(self) -> None:
        client = _client()
        response = {
            "items": [
                {"metadata": {"name": "gp3-csi", "annotations": {"storageclass.kubernetes.io/is-default-class": "true"}}},
                {"metadata": {"name": "gp2-csi"}},
            ]
        }
        with mock.patch.object(client, "do_request", return_value=response) as do_request:
            classes = client.list_storage_classes()
        do_request.assert_called_once_with("GET", "/apis/storage.k8s.io/v1/storageclasses")
        self.assertEqual(
            classes,
            [oshift_types.StorageClass("gp3-csi", True), oshift_types.StorageClass("gp2-csi", False)],
        )

    def test_list_storage_classes_without_permission_is_empty(self) -> None:
        client = _client()
        with mock.patch.object(
            client, "do_request", side_effect=oshift_exceptions.OpenshiftPermissionError("forbidden")
        ):
            self.assertEqual(client.list_storage_classes(), [])

    def test_list_instance_types(self) -> None:
        client = _client()
        response = {
            "items": [
                {"metadata": {"name": "u1.large"}, "spec": {"cpu": {"guest": 2}, "memory": {"guest": "8Gi"}}},
                {"metadata": {"name": "u1.small"}, "spec": {"cpu": {"guest": 1}, "memory": {"guest": "2Gi"}}},
            ]
        }
        with mock.patch.object(client, "do_request", return_value=response) as do_request:
            instance_types = client.list_instance_types()
        do_request.assert_called_once_with(
            "GET", "/apis/instancetype.kubevirt.io/v1beta1/virtualmachineclusterinstancetypes"
        )
        self.assertEqual(
            instance_types,
            [oshift_types.InstanceType("u1.large", 2, "8Gi"), oshift_types.InstanceType("u1.small", 1, "2Gi")],
        )

    def test_list_instance_types_without_permission_is_empty(self) -> None:
        client = _client()
        with mock.patch.object(
            client, "do_request", side_effect=oshift_exceptions.OpenshiftPermissionError("forbidden")
        ):
            self.assertEqual(client.list_instance_types(), [])

    def test_list_networks(self) -> None:
        client = _client()
        response = {"items": [{"metadata": {"name": "vlan10", "namespace": "ns"}}]}
        with mock.patch.object(client, "do_request", return_value=response) as do_request:
            networks = client.list_networks()
        do_request.assert_called_once_with(
            "GET", "/apis/k8s.cni.cncf.io/v1/namespaces/ns/network-attachment-definitions"
        )
        self.assertEqual(networks, ["ns/vlan10"])

    def test_list_networks_without_multus_is_empty(self) -> None:
        client = _client()
        with mock.patch.object(
            client, "do_request", side_effect=oshift_exceptions.OpenshiftNotFoundError("no multus")
        ):
            self.assertEqual(client.list_networks(), [])


class TestOpenshiftMachineChoices(UDSTransactionTestCase):
    def test_choices_start_with_same_as_template(self) -> None:
        api = mock.MagicMock()
        api.list_storage_classes.return_value = [oshift_types.StorageClass("gp3-csi", True)]
        api.list_instance_types.return_value = [oshift_types.InstanceType("u1.large", 2, "8Gi")]
        api.list_networks.return_value = ["ns/vlan10"]
        result = {item["name"]: item["choices"] for item in helpers.machine_choices(api)}

        self.assertEqual([c.id for c in result["storage_class"]], ["", "gp3-csi"])
        self.assertEqual([c.id for c in result["instance_type"]], ["", "u1.large"])
        self.assertEqual([c.id for c in result["network"]], ["", oshift_types.POD_NETWORK, "ns/vlan10"])
        self.assertIn("2", result["instance_type"][1].text)
        self.assertIn("8Gi", result["instance_type"][1].text)


class TestOpenshiftServiceMachineOptions(UDSTransactionTestCase):
    def test_machine_options_from_fields(self) -> None:
        service = fixtures.create_service()
        service.storage_class.value = "gp2-csi"
        service.cores.value = 4
        service.memory.value = 8192
        service.network.value = "ns/vlan10"
        self.assertEqual(
            service.machine_options(),
            oshift_types.MachineOptions(storage_class="gp2-csi", cores=4, memory_mb=8192, network="ns/vlan10"),
        )

    def test_new_service_defaults_keep_template(self) -> None:
        self.assertEqual(fixtures.create_service().machine_options(), oshift_types.MachineOptions())

    def test_instance_type_with_cores_is_rejected(self) -> None:
        service = fixtures.create_service()
        values = {**fixtures.SERVICE_VALUES_DICT, "instance_type": "u1.large", "cores": 4}
        service.instance_type.value = "u1.large"
        service.cores.value = 4
        with self.assertRaises(exceptions.ui.ValidationError):
            service.initialize(values)

    def test_instance_type_with_memory_is_rejected(self) -> None:
        service = fixtures.create_service()
        values = {**fixtures.SERVICE_VALUES_DICT, "instance_type": "u1.large", "memory": 2048}
        service.instance_type.value = "u1.large"
        service.memory.value = 2048
        with self.assertRaises(exceptions.ui.ValidationError):
            service.initialize(values)

    def test_publication_and_userservice_pass_options(self) -> None:
        with fixtures.patched_provider() as prov:
            create_vm = typing.cast(mock.MagicMock, prov.api.create_vm_from_pvc)
            service = fixtures.create_service(provider=prov)
            service.cores.value = 3
            publication = fixtures.create_publication(service=service)
            publication.op_create()
            self.assertEqual(create_vm.call_args.kwargs["options"], oshift_types.MachineOptions(cores=3))
            userservice = fixtures.create_userservice(service=service, publication=publication)
            userservice.op_create()
            self.assertEqual(create_vm.call_args.kwargs["options"], oshift_types.MachineOptions(cores=3))
