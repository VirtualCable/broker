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
Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import logging
import typing
from unittest import mock

from tests.utils import vars
from tests.utils.test import UDSTransactionTestCase
from uds.services.OpenShift.openshift import client as openshift_client
from uds.services.OpenShift.openshift import exceptions as openshift_exceptions

logger: logging.Logger = logging.getLogger(__name__)

SECURE_SESSION = "uds.services.OpenShift.openshift.client.security.secure_requests_session"


class TestOpenshiftClient(UDSTransactionTestCase):
    """Tests for operations with OpenShiftClient."""

    os_client: openshift_client.OpenshiftClient
    test_vm: str = "vm-test"
    test_pool: str = ""
    test_storage: str = ""

    @typing.override
    def setUp(self) -> None:
        """
        Set up OpenShift client and test variables for each test.
        Skips tests if required variables are missing.
        """
        v = vars.get_vars("openshift")
        if not v:
            self.skipTest("No OpenShift test variables found")
        self.os_client = openshift_client.OpenshiftClient(
            cluster_url=v["cluster_url"],
            api_url=v["api_url"],
            username=v["username"],
            password=v["password"],
            namespace=v["namespace"],
            timeout=int(v["timeout"]),
            verify_ssl=v["verify_ssl"] == "true",
        )
        self.test_vm = v.get("test_vm", "")
        self.test_pool = v.get("test_pool", "")
        self.test_storage = v.get("test_storage", "")

    # --- Token/API Tests ---
    def test_get_token(self) -> None:
        """
        Test that get_token returns a valid token string.
        """
        token = self.os_client.get_token()
        self.assertIsNotNone(token)

    def test_get_api_url(self) -> None:
        """
        Test that get_api_url constructs a valid URL with path and parameters.
        """
        url = self.os_client.get_api_url("/test/path", ("param1", "value1"))
        self.assertIn("/test/path", url)
        self.assertIn("param1=value1", url)

    def test_get_api_url_invalid(self) -> None:
        """
        Test that get_api_url works with an invalid path.
        """
        url = self.os_client.get_api_url("/invalid/path", ("param", "value"))
        self.assertIn("/invalid/path", url)

    # --- VM Listing/Info Tests ---
    def test_list_vms(self) -> None:
        """
        Test that list_vms returns a list and get_vm_info works for listed VMs.
        """
        vms_iter = self.os_client.list_vms()
        vms = list(vms_iter)
        self.assertIsInstance(vms, list)
        if len(vms) > 1:
            vm = vms[1]
            if hasattr(vm, "name"):
                info = self.os_client.get_vm_info(vm.name)
                self.assertIsNotNone(info)

    def test_list_vms_and_check_fields(self) -> None:
        """
        Test that all VMs returned by list_vms have required fields.
        """
        vms = list(self.os_client.list_vms())
        self.assertIsInstance(vms, list)
        for vm in vms:
            self.assertTrue(hasattr(vm, "name"))
            self.assertTrue(hasattr(vm, "namespace"))

    def test_get_vm_info_invalid(self) -> None:
        """
        Test that get_vm_info raises OpenshiftNotFoundError for an invalid VM name.
        """
        from uds.services.OpenShift.openshift import exceptions

        with self.assertRaises(exceptions.OpenshiftNotFoundError):
            self.os_client.get_vm_info("nonexistent-vm")

    def test_get_vm_info(self) -> None:
        """
        Test that get_vm_info returns info or None for a valid VM name.
        """
        if not self.test_vm:
            self.skipTest("No test_vm specified")
        info = self.os_client.get_vm_info(self.test_vm)
        self.assertTrue(hasattr(info, "name"))

    # --- VM Lifecycle and Actions ---
    def test_vm_lifecycle(self) -> None:
        """
        Test VM lifecycle actions: start, stop, delete (skipped in shared environments).
        """
        # self.skipTest('Skip this test to avoid issues in shared environments')
        if not self.test_vm:
            self.skipTest("No test_vm specified in test-vars.ini")
        self.assertTrue(self.os_client.start_vm(self.test_vm))
        self.assertTrue(self.os_client.stop_vm(self.test_vm))
        # self.assertTrue(self.os_client.delete_vm(self.test_vm))

    def test_delete_vm_invalid(self) -> None:
        """
        Test that delete_vm returns False for an invalid VM name.
        """
        self.assertTrue(self.os_client.delete_vm("nonexistent-vm"))

    # --- DataVolume Tests ---
    # --- DataVolume Tests ---
    def test_datavolume_phase(self) -> None:
        """
        Test that get_datavolume_phase returns a string for a valid datavolume.
        """
        phase = self.os_client.get_datavolume_phase("test-dv")
        self.assertIsInstance(phase, str)

    def test_datavolume_phase_invalid(self) -> None:
        """
        Test that get_datavolume_phase returns a string for an invalid datavolume.
        """
        phase = self.os_client.get_datavolume_phase("nonexistent-dv")
        self.assertIsInstance(phase, str)


class TestOpenshiftClientToken(UDSTransactionTestCase):
    """Offline tests for token retrieval and session/token caching."""

    def _client(self) -> openshift_client.OpenshiftClient:
        return openshift_client.OpenshiftClient(
            cluster_url="https://oauth-openshift.apps-crc.testing",
            api_url="https://api.crc.testing:6443",
            username="kubeadmin",
            password="test-password",
            namespace="default",
        )

    def _redirect_response(self, token: str = "a-token") -> mock.Mock:
        response = mock.Mock()
        response.status_code = 302
        response.headers = {
            "Location": f"https://oauth-openshift.apps-crc.testing/oauth/token/implicit"
            f"#access_token={token}&expires_in=86400&token_type=Bearer"
        }
        return response

    def test_get_token_from_redirect_fragment(self) -> None:
        client = self._client()
        with mock.patch(SECURE_SESSION) as secure_session:
            secure_session.return_value.get.return_value = self._redirect_response()
            self.assertEqual(client.get_token(), "a-token")
            # verify_ssl must be honored, not hardcoded to False
            self.assertFalse(secure_session.call_args.kwargs["verify"])
            self.assertFalse(secure_session.return_value.get.call_args.kwargs["allow_redirects"])

    def test_get_token_on_non_redirect_raises_auth_error(self) -> None:
        response = mock.Mock()
        response.status_code = 401
        response.headers = {}
        client = self._client()
        with mock.patch(SECURE_SESSION) as secure_session:
            secure_session.return_value.get.return_value = response
            with self.assertRaises(openshift_exceptions.OpenshiftAuthError):
                client.get_token()

    def test_session_reconnects_on_every_access(self) -> None:
        client = self._client()
        with mock.patch.object(client, "get_token", return_value="a-token") as get_token:
            first = client.session
            second = client.session
            self.assertIsNot(first, second)
            self.assertEqual(get_token.call_count, 2)

    def test_connect_refetches_token_after_invalidation(self) -> None:
        client = self._client()
        with mock.patch.object(client, "get_token", return_value="a-token") as get_token:
            client.connect()
            client._session = None  # what do_request does on a 401
            client.connect()
            self.assertEqual(get_token.call_count, 2)

    def test_connect_refetches_token_when_forced(self) -> None:
        client = self._client()
        with mock.patch.object(client, "get_token", return_value="a-token") as get_token:
            client.connect()
            client.connect(force=True)
            self.assertEqual(get_token.call_count, 2)


class TestOpenshiftClientMigratableVolumes(UDSTransactionTestCase):
    """LiveMigration needs the cloned volume to be attached from the destination node,
    so DataVolumes created for VMs whose template has evictionStrategy=LiveMigrate
    must use ReadWriteMany access modes (otherwise OpenShift flags the VM as
    "Not migratable" and emits the VMCannotBeEvicted alert). When the template
    does NOT request LiveMigration we keep ReadWriteOnce so RWO-only storage
    classes still work."""

    def _client(self) -> openshift_client.OpenshiftClient:
        return openshift_client.OpenshiftClient(
            cluster_url="https://oauth-openshift.apps-crc.testing",
            api_url="https://api.crc.testing:6443",
            username="kubeadmin",
            password="test-password",
            namespace="default",
        )

    def _source_vm(self, eviction_strategy: str | None) -> dict[str, typing.Any]:
        template_spec: dict[str, typing.Any] = {
            "volumes": [{"dataVolume": {"name": "src-dv"}}],
            "domain": {"devices": {"interfaces": [{"macAddress": "aa:bb"}]}},
        }
        if eviction_strategy is not None:
            template_spec["evictionStrategy"] = eviction_strategy
        return {
            "metadata": {"name": "tpl-win11", "resourceVersion": "1"},
            "spec": {"running": True, "template": {"spec": template_spec}},
        }

    def _source_pvc(self) -> dict[str, typing.Any]:
        return {
            "status": {"capacity": {"storage": "45Gi"}},
            "spec": {
                "storageClassName": "px-rwx-block-kubevirt",
                "volumeMode": "Block",
            },
        }

    def test_clone_pvc_with_datavolume_defaults_to_read_write_once(self) -> None:
        client = self._client()
        with mock.patch.object(client, "do_request", return_value={}) as do_request:
            client.clone_pvc_with_datavolume(
                source_pvc_name="src-pvc",
                cloned_pvc_name="cloned-pvc",
                storage_class="px-rwx-block-kubevirt",
                storage_size="45Gi",
            )
        sent_body = do_request.call_args.kwargs["data"]
        self.assertEqual(sent_body["spec"]["pvc"]["accessModes"], ["ReadWriteOnce"])

    def test_clone_pvc_with_datavolume_respects_explicit_access_modes(self) -> None:
        client = self._client()
        with mock.patch.object(client, "do_request", return_value={}) as do_request:
            client.clone_pvc_with_datavolume(
                source_pvc_name="src-pvc",
                cloned_pvc_name="cloned-pvc",
                storage_class="px-rwx-block-kubevirt",
                storage_size="45Gi",
                access_modes=["ReadWriteMany"],
            )
        sent_body = do_request.call_args.kwargs["data"]
        self.assertEqual(sent_body["spec"]["pvc"]["accessModes"], ["ReadWriteMany"])

    def _create_vm(self, eviction_strategy: str | None) -> dict[str, typing.Any]:
        client = self._client()
        vm_obj = self._source_vm(eviction_strategy)
        pvc_obj = self._source_pvc()

        def fake_do_request(method: str, path: str, **kwargs: typing.Any) -> typing.Any:
            if "virtualmachines/tpl-win11" in path:
                return vm_obj
            if "persistentvolumeclaims/src-pvc" in path:
                return pvc_obj
            return {}

        with mock.patch.object(client, "do_request", side_effect=fake_do_request) as do_request:
            client.create_vm_from_pvc(
                source_vm_name="tpl-win11",
                new_vm_name="udsglz003",
                new_dv_name="udsglz003-disk",
                source_pvc_name="src-pvc",
            )
        return do_request.call_args.kwargs["data"]

    def test_create_vm_from_pvc_uses_read_write_many_when_eviction_strategy_is_live_migrate(self) -> None:
        body = self._create_vm("LiveMigrate")
        dvt = body["spec"]["dataVolumeTemplates"][0]
        self.assertEqual(dvt["spec"]["pvc"]["accessModes"], ["ReadWriteMany"])
        self.assertEqual(dvt["spec"]["pvc"]["storageClassName"], "px-rwx-block-kubevirt")
        self.assertEqual(dvt["spec"]["pvc"]["volumeMode"], "Block")

    def test_create_vm_from_pvc_uses_read_write_once_without_eviction_strategy(self) -> None:
        body = self._create_vm(None)
        dvt = body["spec"]["dataVolumeTemplates"][0]
        self.assertEqual(dvt["spec"]["pvc"]["accessModes"], ["ReadWriteOnce"])

    def test_create_vm_from_pvc_uses_read_write_once_with_non_live_eviction_strategy(self) -> None:
        # "None" or any value other than "LiveMigrate" must NOT trigger RWX.
        body = self._create_vm("None")
        dvt = body["spec"]["dataVolumeTemplates"][0]
        self.assertEqual(dvt["spec"]["pvc"]["accessModes"], ["ReadWriteOnce"])

    def test_access_modes_for_live_migration_helper(self) -> None:
        client = self._client()
        with mock.patch.object(client, "do_request", return_value=self._source_vm("LiveMigrate")):
            self.assertEqual(
                client._access_modes_for_live_migration("tpl-win11"),
                ["ReadWriteMany"],
            )
        with mock.patch.object(client, "do_request", return_value=self._source_vm(None)):
            self.assertEqual(
                client._access_modes_for_live_migration("tpl-win11"),
                ["ReadWriteOnce"],
            )

    def test_copy_vm_same_size_uses_read_write_many_only_when_live_migrate(self) -> None:
        client = self._client()
        captured: list[dict[str, typing.Any]] = []

        def fake_do_request(method: str, path: str, **kwargs: typing.Any) -> typing.Any:
            if method == "GET" and "virtualmachines/tpl-win11" in path:
                return self._source_vm(current_eviction[0])
            if method == "GET" and "persistentvolumeclaims/src-pvc" in path:
                return self._source_pvc()
            if method == "POST" and "/datavolumes" in path:
                captured.append({"which": "dv", "data": kwargs["data"]})
                return {}
            if method == "POST" and "/virtualmachines" in path:
                captured.append({"which": "vm", "data": kwargs["data"]})
                return {}
            return {}

        with (
            mock.patch.object(client, "do_request", side_effect=fake_do_request),
            mock.patch.object(client, "get_pvc_size", return_value="45Gi"),
            mock.patch.object(client, "get_vm_pvc_or_dv_name", return_value=("src-pvc", "pvc")),
        ):
            current_eviction: list[str | None] = ["LiveMigrate"]
            client.copy_vm_same_size(
                source_vm_name="tpl-win11",
                new_vm_name="udsglz003",
                storage_class="px-rwx-block-kubevirt",
            )
        dv_call = next(c for c in captured if c["which"] == "dv")
        vm_call = next(c for c in captured if c["which"] == "vm")
        self.assertEqual(dv_call["data"]["spec"]["pvc"]["accessModes"], ["ReadWriteMany"])
        self.assertEqual(
            vm_call["data"]["spec"]["dataVolumeTemplates"][0]["spec"]["pvc"]["accessModes"],
            ["ReadWriteMany"],
        )

        captured.clear()
        with (
            mock.patch.object(client, "do_request", side_effect=fake_do_request),
            mock.patch.object(client, "get_pvc_size", return_value="45Gi"),
            mock.patch.object(client, "get_vm_pvc_or_dv_name", return_value=("src-pvc", "pvc")),
        ):
            current_eviction[0] = None
            client.copy_vm_same_size(
                source_vm_name="tpl-win11",
                new_vm_name="udsglz003",
                storage_class="px-rwx-block-kubevirt",
            )
        dv_call = next(c for c in captured if c["which"] == "dv")
        vm_call = next(c for c in captured if c["which"] == "vm")
        self.assertEqual(dv_call["data"]["spec"]["pvc"]["accessModes"], ["ReadWriteOnce"])
        self.assertEqual(
            vm_call["data"]["spec"]["dataVolumeTemplates"][0]["spec"]["pvc"]["accessModes"],
            ["ReadWriteOnce"],
        )
