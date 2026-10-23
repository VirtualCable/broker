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
import uuid
from unittest import mock

from uds.core import environment, exceptions, types
from uds.services.OpenShift import helpers, provider
from uds.services.OpenShift import deployment_linked, service_linked
from uds.services.OpenShift.openshift import client as oshift_client
from uds.services.OpenShift.openshift import exceptions as oshift_exceptions
from uds.services.OpenShift.openshift import types as oshift_types

from tests.utils.test import UDSTransactionTestCase

from . import fixtures

PUB_VM: dict[str, typing.Any] = {
    "apiVersion": "kubevirt.io/v1",
    "kind": "VirtualMachine",
    "metadata": {"name": "pub-1", "namespace": "ns", "uid": "u", "resourceVersion": "1"},
    "spec": {
        "runStrategy": "Halted",
        "dataVolumeTemplates": [{"metadata": {"name": "pub-1-disk"}, "spec": {"pvc": {}}}],
        "template": {
            "spec": {
                "domain": {
                    "cpu": {"cores": 2},
                    "memory": {"guest": "4Gi"},
                    "devices": {
                        "disks": [{"name": "rootdisk", "disk": {"bus": "virtio"}}],
                        "interfaces": [{"name": "default", "masquerade": {}, "macAddress": "02:00:00:00:00:01"}],
                    },
                },
                "networks": [{"name": "default", "pod": {}}],
                "volumes": [
                    {"name": "rootdisk", "dataVolume": {"name": "pub-1-disk"}},
                    {"name": "cloudinit", "cloudInitNoCloud": {"userData": "#cloud-config"}},
                ],
            }
        },
    },
}


def _client() -> oshift_client.OpenshiftClient:
    return oshift_client.OpenshiftClient(
        cluster_url="https://oauth.example",
        api_url="https://api.example:6443",
        username="user",
        password="pass",
        namespace="ns",
    )


def _storage_class(provisioner: str) -> dict[str, typing.Any]:
    return {"metadata": {"name": "sc"}, "provisioner": provisioner}


class TestCopyOnWriteDetection(UDSTransactionTestCase):
    def test_known_copy_on_write_provisioners(self) -> None:
        for provisioner in (
            "openshift-storage.rbd.csi.ceph.com",
            "rbd.csi.ceph.com",
            "csi.trident.netapp.io",
            "pxd.portworx.com",
        ):
            self.assertTrue(oshift_types.is_copy_on_write_provisioner(provisioner), provisioner)

    def test_full_copy_provisioners(self) -> None:
        for provisioner in ("ebs.csi.aws.com", "kubevirt.io.hostpath-provisioner", "nfs.csi.k8s.io", ""):
            self.assertFalse(oshift_types.is_copy_on_write_provisioner(provisioner), provisioner)

    def test_vm_storage_info_reads_class_of_the_vm_disk(self) -> None:
        client = _client()

        def fake_do_request(method: str, path: str, **kwargs: typing.Any) -> typing.Any:
            if "/virtualmachines/" in path:
                return copy.deepcopy(PUB_VM)
            if "/persistentvolumeclaims/" in path:
                return {"spec": {"storageClassName": "ocs-rbd", "accessModes": ["ReadWriteMany"]}}
            if "/storageclasses/" in path:
                return _storage_class("openshift-storage.rbd.csi.ceph.com")
            raise AssertionError(path)

        with mock.patch.object(client, "do_request", side_effect=fake_do_request):
            info = client.get_vm_storage_info("pub-1")
        self.assertEqual(
            info,
            oshift_types.StorageInfo(
                storage_class="ocs-rbd",
                provisioner="openshift-storage.rbd.csi.ceph.com",
                shared=True,
            ),
        )
        self.assertTrue(info.copy_on_write)

    def test_vm_storage_info_without_storage_class_access(self) -> None:
        client = _client()

        def fake_do_request(method: str, path: str, **kwargs: typing.Any) -> typing.Any:
            if "/virtualmachines/" in path:
                return copy.deepcopy(PUB_VM)
            if "/persistentvolumeclaims/" in path:
                return {"spec": {"storageClassName": "gp3-csi", "accessModes": ["ReadWriteOnce"]}}
            raise oshift_exceptions.OpenshiftPermissionError("forbidden")

        with mock.patch.object(client, "do_request", side_effect=fake_do_request):
            info = client.get_vm_storage_info("pub-1")
        # Unknown provisioner: persistent mode is not offered
        self.assertEqual(info.provisioner, "")
        self.assertFalse(info.copy_on_write)
        self.assertFalse(info.shared)


class TestEphemeralVmBody(UDSTransactionTestCase):
    def _create(self) -> dict[str, typing.Any]:
        client = _client()

        def fake_do_request(method: str, path: str, **kwargs: typing.Any) -> typing.Any:
            if method == "GET" and "/virtualmachines/" in path:
                return copy.deepcopy(PUB_VM)
            if method == "GET":
                raise AssertionError(path)
            return {}

        with mock.patch.object(client, "do_request", side_effect=fake_do_request) as do_request:
            client.create_ephemeral_vm(
                "pub-1", "lc-001", "pub-1-disk", options=oshift_types.MachineOptions(cores=4)
            )
        post = next(c for c in do_request.call_args_list if c.args[0] == "POST")
        return post.kwargs["data"]

    def test_root_disk_is_ephemeral_over_the_publication_pvc(self) -> None:
        body = self._create()
        spec = body["spec"]
        self.assertNotIn("dataVolumeTemplates", spec)
        volumes = spec["template"]["spec"]["volumes"]
        self.assertEqual(
            volumes[0],
            {"name": "rootdisk", "ephemeral": {"persistentVolumeClaim": {"claimName": "pub-1-disk", "readOnly": True}}},
        )
        # Volumes that are not the publication disk are kept
        self.assertEqual(volumes[1]["name"], "cloudinit")

    def test_ephemeral_vm_is_started_named_and_gets_machine_options(self) -> None:
        body = self._create()
        self.assertEqual(body["metadata"]["name"], "lc-001")
        self.assertNotIn("uid", body["metadata"])
        self.assertNotIn("resourceVersion", body["metadata"])
        self.assertEqual(body["spec"]["runStrategy"], "Always")
        self.assertEqual(body["spec"]["template"]["spec"]["domain"]["cpu"]["cores"], 4)
        iface = body["spec"]["template"]["spec"]["domain"]["devices"]["interfaces"][0]
        self.assertNotIn("macAddress", iface)


def _create_linked_service(prov: provider.OpenshiftProvider, **values: typing.Any) -> service_linked.OpenshiftLinkedService:
    uuid_ = str(uuid.uuid4())
    data = {**fixtures.SERVICE_VALUES_DICT, "mode": oshift_types.LinkedCloneMode.NON_PERSISTENT, **values}
    return service_linked.OpenshiftLinkedService(
        environment=environment.Environment.private_environment(uuid_),
        provider=prov,
        values=data,
        uuid=uuid_,
    )


class TestLinkedService(UDSTransactionTestCase):
    def test_offered_by_the_provider(self) -> None:
        self.assertIn(service_linked.OpenshiftLinkedService, provider.OpenshiftProvider.offers)

    def test_no_l2_cache_for_non_persistent_machines(self) -> None:
        # L2 cache keeps machines stopped, and stopping an ephemeral VM discards its disk
        self.assertFalse(service_linked.OpenshiftLinkedService.uses_cache_l2)

    def test_mode_choices_with_copy_on_write_storage(self) -> None:
        api = mock.MagicMock()
        api.get_vm_storage_info.return_value = oshift_types.StorageInfo("ocs-rbd", "rbd.csi.ceph.com", True)
        result = {item["name"]: item["choices"] for item in helpers.linked_mode_choices(api, "tpl")}
        ids = [c.id for c in result["mode"]]
        self.assertEqual(
            ids, [oshift_types.LinkedCloneMode.PERSISTENT, oshift_types.LinkedCloneMode.NON_PERSISTENT]
        )

    def test_mode_choices_with_full_copy_storage_only_non_persistent(self) -> None:
        api = mock.MagicMock()
        api.get_vm_storage_info.return_value = oshift_types.StorageInfo("gp3-csi", "ebs.csi.aws.com", False)
        result = {item["name"]: item["choices"] for item in helpers.linked_mode_choices(api, "tpl")}
        self.assertEqual([c.id for c in result["mode"]], [oshift_types.LinkedCloneMode.NON_PERSISTENT])
        # The only choice explains why persistent is not offered
        self.assertIn("gp3-csi", result["mode"][0].text)

    def test_persistent_rejected_on_full_copy_storage(self) -> None:
        with fixtures.patched_provider() as prov:
            typing.cast(mock.MagicMock, prov.api).get_vm_storage_info.return_value = oshift_types.StorageInfo(
                "gp3-csi", "ebs.csi.aws.com", False
            )
            with self.assertRaises(exceptions.ui.ValidationError):
                _create_linked_service(prov, mode=oshift_types.LinkedCloneMode.PERSISTENT)

    def test_persistent_accepted_on_copy_on_write_storage(self) -> None:
        with fixtures.patched_provider() as prov:
            typing.cast(mock.MagicMock, prov.api).get_vm_storage_info.return_value = oshift_types.StorageInfo(
                "ocs-rbd", "rbd.csi.ceph.com", True
            )
            service = _create_linked_service(prov, mode=oshift_types.LinkedCloneMode.PERSISTENT)
            self.assertTrue(service.is_persistent())

    def test_storage_class_override_rejected_for_linked_clones(self) -> None:
        # The clone must stay on the template storage, the only one known to share blocks
        with fixtures.patched_provider() as prov:
            typing.cast(mock.MagicMock, prov.api).get_vm_storage_info.return_value = oshift_types.StorageInfo(
                "ocs-rbd", "rbd.csi.ceph.com", True
            )
            with self.assertRaises(exceptions.ui.ValidationError):
                _create_linked_service(prov, storage_class="other")

    def test_non_persistent_deploy_creates_ephemeral_vm(self) -> None:
        with fixtures.patched_provider() as prov:
            api = typing.cast(mock.MagicMock, prov.api)
            service = _create_linked_service(prov, cores=2)
            publication = fixtures.create_publication(service=service)
            userservice = deployment_linked.OpenshiftLinkedUserService(
                environment=environment.Environment.private_environment(str(uuid.uuid4())),
                service=service,
                publication=publication,
                uuid=str(uuid.uuid4()),
            )
            userservice.op_create()
            api.create_ephemeral_vm.assert_called_once()
            kwargs = api.create_ephemeral_vm.call_args.kwargs
            self.assertEqual(kwargs["source_vm_name"], publication._name)
            self.assertEqual(kwargs["options"], oshift_types.MachineOptions(cores=2))
            api.create_vm_from_pvc.assert_not_called()

    def test_non_persistent_create_checker_waits_for_mac_without_datavolume(self) -> None:
        with fixtures.patched_provider() as prov:
            api = typing.cast(mock.MagicMock, prov.api)
            service = _create_linked_service(prov)
            userservice = deployment_linked.OpenshiftLinkedUserService(
                environment=environment.Environment.private_environment(str(uuid.uuid4())),
                service=service,
                publication=fixtures.create_publication(service=service),
                uuid=str(uuid.uuid4()),
            )
            userservice._name = "lc-001"
            api.get_vm_interfaces.return_value = []
            self.assertEqual(userservice.op_create_checker(), types.states.TaskState.RUNNING)
            api.get_vm_interfaces.return_value = [oshift_types.Interface("default", "02:00:00:00:00:02", "10.0.0.2")]
            self.assertEqual(userservice.op_create_checker(), types.states.TaskState.FINISHED)
            api.get_datavolume_phase.assert_not_called()

    def test_persistent_deploy_clones_the_disk(self) -> None:
        with fixtures.patched_provider() as prov:
            api = typing.cast(mock.MagicMock, prov.api)
            api.get_vm_storage_info.return_value = oshift_types.StorageInfo("ocs-rbd", "rbd.csi.ceph.com", True)
            service = _create_linked_service(prov, mode=oshift_types.LinkedCloneMode.PERSISTENT)
            userservice = deployment_linked.OpenshiftLinkedUserService(
                environment=environment.Environment.private_environment(str(uuid.uuid4())),
                service=service,
                publication=fixtures.create_publication(service=service),
                uuid=str(uuid.uuid4()),
            )
            userservice.op_create()
            api.create_vm_from_pvc.assert_called_once()
            api.create_ephemeral_vm.assert_not_called()


class TestEphemeralDeletion(UDSTransactionTestCase):
    def test_ephemeral_vm_has_no_own_disk_to_wait_for(self) -> None:
        # The ephemeral layer lives in the launcher pod: once the VM is gone, nothing is left.
        # The publication PVC behind it must not be taken as the VM disk.
        client = _client()
        vm = copy.deepcopy(PUB_VM)
        vm["spec"]["template"]["spec"]["volumes"][0] = {
            "name": "rootdisk",
            "ephemeral": {"persistentVolumeClaim": {"claimName": "pub-1-disk", "readOnly": True}},
        }
        del vm["spec"]["template"]["spec"]["volumes"][1]
        with mock.patch.object(client, "do_request", return_value=vm):
            with self.assertRaises(Exception):
                client.get_vm_pvc_or_dv_name("lc-001")
