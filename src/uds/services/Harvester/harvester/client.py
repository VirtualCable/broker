#
# Copyright (c) 2026 Virtual Cable S.L.U.
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
import hashlib
import logging
import typing
from dataclasses import dataclass

import requests

from . import exceptions as hv_exceptions
from . import types as hv_types

logger: logging.Logger = logging.getLogger(__name__)

API_HARVESTER = 'apis/harvesterhci.io/v1beta1'
API_KUBEVIRT = 'apis/kubevirt.io/v1'

KUBEVIRT_VM_KIND = 'VirtualMachine'


def _raise_for_status_code(response: requests.Response, what: str) -> None:
    """Maps a non-ok kubernetes response to our exceptions."""
    status = response.status_code
    if 200 <= status < 300:
        return
    detail = ''
    try:
        payload = response.json()
    except (requests.exceptions.JSONDecodeError, ValueError):
        detail = response.text[:200]
    else:
        if isinstance(payload, dict):
            detail = str(payload.get('message', ''))
    if status == 401:
        raise hv_exceptions.HarvesterAuthError(f'{what}: authentication failed ({detail})')
    if status == 403:
        raise hv_exceptions.HarvesterPermissionError(f'{what}: permission denied ({detail})')
    if status == 404:
        raise hv_exceptions.HarvesterNotFoundError(f'{what} not found ({detail})')
    if status == 429 or 500 <= status <= 599:
        # Temporary server-side problems are retried by the Dynamic* machinery
        raise hv_exceptions.HarvesterConnectionError(
            f'{what}: temporary error {status} ({detail})'
        )
    raise hv_exceptions.HarvesterError(f'{what}: error {status} ({detail})')


@dataclass
class HarvesterClient:
    """
    HTTP client for Harvester (SUSE Virtualization).

    Works with the stock APIs of the product:
      * harvesterhci.io/v1beta1 (templates, images, ...)
      * kubevirt.io/v1 (virtualmachines, virtualmachineinstances)

    Authentication is a Rancher/Harvester Bearer token (created in the HTML5 GUI,
    User -> Generate API Key) or a user session token.
    """

    endpoint: str
    api_token: str
    namespace: str = 'default'
    verify_ssl: bool = True
    timeout: int = 10

    _requests_session: requests.Session | None = None

    def _session(self) -> requests.Session:
        if self._requests_session is None:
            session = requests.Session()
            session.verify = self.verify_ssl
            session.headers.update(
                {
                    'Authorization': f'Bearer {self.api_token}',
                    'Accept': 'application/json',
                }
            )
            self._requests_session = session
        return self._requests_session

    def cache_key(self) -> str:
        token_digest = hashlib.sha256(self.api_token.encode()).hexdigest()
        return f'{self.endpoint}|{token_digest}|{self.namespace}'

    def clear_cache(self) -> None:
        self._requests_session = None

    def _get_url(self, path: str, *parameters: tuple[str, str]) -> str:
        url = f'{self.endpoint}/{path}'
        if parameters:
            url += '?' + '&'.join(f'{k!s}={v!s}' for k, v in parameters)
        return url

    def do_request(
        self,
        method: str,
        url_path: str,
        json_body: typing.Any = None,
        *,
        patch: bool = False,
        what: str = 'request',
    ) -> requests.Response:
        """
        Performs a request to the harvester server.

        Args:
            method: HTTP method
            url_path: path starting from the API root, that is, after the host
            json_body: body to send as json, if any
            patch: if True, sends the body as a merge patch content type
            what: description used in errors

        Returns:
            The response
        """
        try:
            headers = None
            if json_body is not None:
                headers = {
                    'Content-Type': (
                        'application/merge-patch+json' if patch else 'application/json'
                    )
                }
            response = self._session().request(
                method,
                self._get_url(url_path),
                json=json_body,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.exceptions.ConnectionError:
            raise hv_exceptions.HarvesterConnectionError(
                f'{what}: cannot connect to {self.endpoint}'
            ) from None
        except requests.exceptions.Timeout:
            raise hv_exceptions.HarvesterConnectionError(f'{what}: timeout') from None
        _raise_for_status_code(response, what)
        return response

    def _get_json(self, url_path: str, what: str, *parameters: tuple[str, str]) -> dict[str, typing.Any]:
        return self.do_request('GET', url_path, what=what).json()

    # --- Connection testing ---

    def test(self) -> bool:
        """Cheap unauthenticated reachability check + auth check."""
        self._session()
        response = self.do_request('GET', 'ping', what='ping')
        # /ping returns 'pong' for everyone; auth needs an authenticated call
        if not response.ok:
            return False
        self._get_json(
            f'{API_HARVESTER}/namespaces/{self.namespace}/virtualmachinetemplates',
            'test',
        )
        return True

    # --- Templates / template versions ---

    def list_templates(self, namespace: str) -> list[hv_types.Template]:
        templates: list[hv_types.Template] = []
        for item in self._get_json(
            f'{API_HARVESTER}/namespaces/{namespace}/virtualmachinetemplates',
            'list templates',
        ).get('items', []):
            spec = item.get('spec', {})
            templates.append(
                hv_types.Template(
                    name=item['metadata']['name'],
                    namespace=item['metadata'].get('namespace', namespace),
                    default_version_id=spec.get('defaultVersionId', ''),
                    description=spec.get('description', ''),
                )
            )
        return templates

    def get_template(self, namespace: str, name: str) -> hv_types.Template:
        item = self._get_json(
            f'{API_HARVESTER}/namespaces/{namespace}/virtualmachinetemplates/{name}',
            'template',
        )
        spec = item.get('spec', {})
        return hv_types.Template(
            name=item['metadata']['name'],
            namespace=item['metadata'].get('namespace', namespace),
            default_version_id=spec.get('defaultVersionId', ''),
            description=spec.get('description', ''),
        )

    def list_template_versions(self, namespace: str, template_name: str) -> list[hv_types.TemplateVersion]:
        """
        Lists the versions of a template, filtering by xmlReference via a field selector.
        """
        result: list[hv_types.TemplateVersion] = []
        # Versions slots are returned all together on harvester; filter here because
        # the version objects do not always carry a label with the template name.
        template_id = f'{namespace}/{template_name}'
        for item in self._get_json(
            f'{API_HARVESTER}/namespaces/{namespace}/virtualmachinetemplateversions',
            'list template versions',
        ).get('items', []):
            if item.get('spec', {}).get('templateId') == template_id:
                result.append(self._version_from_item(item))
        return result

    @staticmethod
    def _version_from_item(item: dict[str, typing.Any]) -> hv_types.TemplateVersion:
        status = item.get('status') or {}
        ready = any(cond.get('type') == 'ready' and cond.get('status') == 'True' for cond in status.get('conditions') or [])
        spec = item.get('spec', {})
        vm = spec.get('vm') or {}
        return hv_types.TemplateVersion(
            name=item['metadata']['name'],
            namespace=item['metadata'].get('namespace', ''),
            template_id=spec.get('templateId', ''),
            version=int(status.get('version') or 0),
            ready=ready,
            vm_spec=vm.get('spec') or {},
            vm_metadata=vm.get('metadata') or {},
            description=spec.get('description', ''),
        )

    def get_template_version(self, namespace: str, version_ref: str) -> hv_types.TemplateVersion:
        """
        Reads a template version. `version_ref` can be a plain version name or a
        full reference "namespace/name" (the format stored in template settings).
        """
        version_name = version_ref.split('/')[-1]
        version_namespace = version_ref.split('/')[0] if '/' in version_ref else namespace
        item = self._get_json(
            f'{API_HARVESTER}/namespaces/{version_namespace}/virtualmachinetemplateversions/{version_name}',
            'template version',
        )
        return self._version_from_item(item)

    def create_template_version(
        self,
        namespace: str,
        template_id: str,
        description: str,
        vm_spec: dict[str, typing.Any],
    ) -> hv_types.TemplateVersion:
        """
        Creates a new version of a template.

        Args:
            namespace: namespace of the template
            template_id: "namespace/name" of the template
            description: description for the version
            vm_spec: kubevirt VirtualMachineSpec to store in the version

        Returns:
            The created version (may not have 'version' assigned yet)
        """
        body = {
            'apiVersion': 'harvesterhci.io/v1beta1',
            'kind': 'VirtualMachineTemplateVersion',
            'metadata': {
                'generateName': 'uds-',
                'namespace': namespace,
            },
            'spec': {
                'templateId': template_id,
                'description': description,
                'keyPairIds': [],
                'vm': {
                    'metadata': {'labels': {'harvesterhci.io/creator': 'harvester'}},
                    'spec': vm_spec,
                },
            },
        }
        item = self.do_request(
            'POST',
            f'{API_HARVESTER}/namespaces/{namespace}/virtualmachinetemplateversions',
            json_body=body,
            what='create template version',
        ).json()
        return self._version_from_item(item)

    def delete_template_version(self, namespace: str, version_ref: str) -> None:
        version_name = version_ref.split('/')[-1]
        version_namespace = version_ref.split('/')[0] if '/' in version_ref else namespace
        try:
            self.do_request(
                'DELETE',
                f'{API_HARVESTER}/namespaces/{version_namespace}/virtualmachinetemplateversions/{version_name}',
                what='delete template version',
            )
        except hv_exceptions.HarvesterNotFoundError:
            pass

    # --- Virtual machines ---

    def create_vm(
        self,
        name: str,
        vm_spec: dict[str, typing.Any],
        running: bool = True,
        vm_metadata: dict[str, typing.Any] | None = None,
    ) -> None:
        """
        Creates a kubevirt VirtualMachine from a template version vm info.

        The full version spec is kept (template, dataVolumeTemplates, volumes...),
        only touching `running` and the interface bindings of the template; the
        spec is deep-copied so callers can reuse it.
        """
        spec = copy.deepcopy(vm_spec)
        spec['running'] = running
        pod_spec = spec.get('template', {}).setdefault('spec', {})
        # kubevirt requires a 1:1 interface/network mapping; templates saved without
        # networks (empty test VMs) get their pod network entry created here
        interfaces = pod_spec.get('domain', {}).setdefault('devices', {}).setdefault('interfaces', [])
        networks = pod_spec.setdefault('networks', [])
        network_names = {n.get('name') for n in networks}
        for iface in interfaces:
            if iface.get('name') not in network_names:
                networks.append({'name': iface['name'], 'pod': {}})
        for iface in interfaces:
            # kubevirt >= 1.4: binding plugins need cluster configuration; the classic
            # masquerade inline binding has no such requirement and matches the pod network
            if 'binding' in iface:
                iface.pop('binding')
            if 'masquerade' not in iface and 'bridge' not in iface and 'sriov' not in iface:
                iface['masquerade'] = {}
        metadata = copy.deepcopy(vm_metadata or {})
        metadata['name'] = name
        metadata['namespace'] = self.namespace
        labels = metadata.setdefault('labels', {})
        labels.setdefault('uds.io/managed', 'true')
        body = {
            'apiVersion': 'kubevirt.io/v1',
            'kind': KUBEVIRT_VM_KIND,
            'metadata': metadata,
            'spec': spec,
        }
        self.do_request(
            'POST',
            f'{API_KUBEVIRT}/namespaces/{self.namespace}/virtualmachines',
            json_body=body,
            what='create VM',
        )

    def get_vm_info(self, name: str) -> hv_types.VMInfo:
        """
        Gets VM info: the desired state (VirtualMachine, always exists if not deleted)
        and the current state (VirtualMachineInstance) if running.
        """
        running = False
        exists = False
        interfaces: list[dict[str, str]] = []
        try:
            self.do_request(
                'GET',
                f'{API_KUBEVIRT}/namespaces/{self.namespace}/virtualmachines/{name}',
                what='read VM',
            )
            exists = True
        except hv_exceptions.HarvesterNotFoundError:
            return hv_types.VMInfo.not_found(name, self.namespace)

        try:
            vmi = self._get_json(
                f'{API_KUBEVIRT}/namespaces/{self.namespace}/virtualmachineinstances/{name}',
                'read VMI',
            )
            phase = (vmi.get('status') or {}).get('phase', '')
            running = phase == 'Running'
            interfaces = [
                {
                    'macAddress': str(i.get('mac') or i.get('macAddress') or ''),
                    'ipAddress': str(i.get('ipAddress') or ''),
                }
                for i in (vmi.get('status') or {}).get('interfaces', [])
            ]
        except hv_exceptions.HarvesterNotFoundError:
            pass
        return hv_types.VMInfo(
            name=name,
            namespace=self.namespace,
            running=running,
            exists=exists,
            interfaces=interfaces,
        )

    def get_vm_interfaces(self, name: str) -> list[dict[str, str]]:
        return self.get_vm_info(name).interfaces

    def set_vm_running(self, name: str, running: bool) -> None:
        self.do_request(
            'PATCH',
            f'{API_KUBEVIRT}/namespaces/{self.namespace}/virtualmachines/{name}',
            json_body={'spec': {'running': running}},
            patch=True,
            what=f'set VM running={running}',
        )

    def delete_vm(self, name: str) -> None:
        """Deletes the VM. The VMI and its volumes die with it."""
        try:
            self.do_request(
                'DELETE',
                f'{API_KUBEVIRT}/namespaces/{self.namespace}/virtualmachines/{name}',
                what='delete VM',
            )
        except hv_exceptions.HarvesterNotFoundError:
            pass  # Already deleted

    def list_vms(self) -> list[str]:
        """
        Lists the names of the VMs managed by Harvester in this namespace.
        """
        items = self._get_json(
            f'{API_KUBEVIRT}/namespaces/{self.namespace}/virtualmachines',
            'list VMs',
        ).get('items', [])
        return [item['metadata']['name'] for item in items if item.get('metadata', {}).get('name')]
