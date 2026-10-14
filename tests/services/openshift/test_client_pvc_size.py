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

from tests.utils.test import UDSTransactionTestCase
from uds.services.OpenShift.openshift import client as openshift_client


class TestOpenshiftClientPvcSize(UDSTransactionTestCase):
    """A clone must be sized from what the source PVC requested. The capacity reported in
    its status is whatever the provisioner decides to grant: hostpath-like provisioners
    report the capacity of the whole pool for every PVC, which would make every clone
    request the full pool."""

    def _client(self) -> openshift_client.OpenshiftClient:
        return openshift_client.OpenshiftClient(
            cluster_url="https://oauth-openshift.apps-crc.testing",
            api_url="https://api.crc.testing:6443",
            username="kubeadmin",
            password="test-password",
            namespace="default",
        )

    def _pvc(self, requested: str | None, capacity: str | None) -> dict[str, typing.Any]:
        pvc: dict[str, typing.Any] = {"spec": {"storageClassName": "hostpath", "volumeMode": "Filesystem"}}
        if requested:
            pvc["spec"]["resources"] = {"requests": {"storage": requested}}
        if capacity:
            pvc["status"] = {"capacity": {"storage": capacity}}
        return pvc

    def test_get_pvc_size_prefers_requested_over_capacity(self) -> None:
        client = self._client()
        with mock.patch.object(client, "do_request", return_value=self._pvc("34144990004", "399Gi")):
            self.assertEqual(client.get_pvc_size("src-pvc"), "34144990004")

    def test_get_pvc_size_falls_back_to_capacity(self) -> None:
        client = self._client()
        with mock.patch.object(client, "do_request", return_value=self._pvc(None, "45Gi")):
            self.assertEqual(client.get_pvc_size("src-pvc"), "45Gi")

    def test_get_pvc_size_raises_without_any_size(self) -> None:
        client = self._client()
        with mock.patch.object(client, "do_request", return_value=self._pvc(None, None)):
            with self.assertRaises(Exception):
                client.get_pvc_size("src-pvc")

    def test_create_vm_from_pvc_requests_the_source_pvc_requested_size(self) -> None:
        client = self._client()
        vm_obj: dict[str, typing.Any] = {
            "metadata": {"name": "tpl", "resourceVersion": "1"},
            "spec": {
                "running": True,
                "template": {
                    "spec": {
                        "volumes": [{"dataVolume": {"name": "src-dv"}}],
                        "domain": {"devices": {"interfaces": [{"macAddress": "aa:bb"}]}},
                    }
                },
            },
        }
        pvc_obj = self._pvc("34144990004", "399Gi")

        def fake_do_request(method: str, path: str, **kwargs: typing.Any) -> typing.Any:
            if "virtualmachines/tpl" in path:
                return vm_obj
            if "persistentvolumeclaims/src-pvc" in path:
                return pvc_obj
            return {}

        with mock.patch.object(client, "do_request", side_effect=fake_do_request) as do_request:
            client.create_vm_from_pvc(
                source_vm_name="tpl",
                new_vm_name="clone",
                new_dv_name="clone-disk",
                source_pvc_name="src-pvc",
            )
        body = do_request.call_args.kwargs["data"]
        requested = body["spec"]["dataVolumeTemplates"][0]["spec"]["pvc"]["resources"]["requests"]["storage"]
        self.assertEqual(requested, "34144990004")
