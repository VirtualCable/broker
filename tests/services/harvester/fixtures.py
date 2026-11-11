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

import contextlib
import copy
import functools
import random
import typing
import uuid
from collections.abc import Callable, Generator
from unittest import mock

from uds.core import environment
from uds.core.ui.user_interface import gui
from uds.services.Harvester import deployment, provider, publication, service
from uds.services.Harvester.harvester import exceptions as hv_exceptions
from uds.services.Harvester.harvester import types as hv_types

DEF_TEMPLATES: list[hv_types.Template] = [
    hv_types.Template(
        name=f'template-{i}',
        namespace='default',
        default_version_id=f'default/template-{i}-v1',
        description=f'test template {i}',
    )
    for i in range(1, 11)
]


def _default_version_for(template: hv_types.Template) -> hv_types.TemplateVersion:
    """Builds the version that the fixture assigns as template default version."""
    return hv_types.TemplateVersion(
        name=template.default_version_id.split('/')[-1],
        namespace=template.namespace,
        template_id=f'{template.namespace}/{template.name}',
        version=1,
        ready=True,
        vm_metadata={'labels': {'harvesterhci.io/creator': 'harvester'}},
        # kubevirt VirtualMachineSpec, as stored by harvester in the version
        vm_spec={
            'running': False,
            'template': {
                'spec': {
                    'domain': {
                        'cpu': {
                            'sockets': 1,
                            'threads': 1,
                            'cores': 1,
                            'limits': {'cpu': '1'},
                        },
                        'resources': {
                            'requests': {'memory': '1Gi'},
                            'limits': {'memory': '1Gi'},
                        },
                        'devices': {'disks': [], 'interfaces': []},
                    },
                },
            },
        },
    )


DEF_VERSIONS: list[hv_types.TemplateVersion] = [
    _default_version_for(t) for t in copy.deepcopy(DEF_TEMPLATES)
]

# kubevirt VM infos for the "created" VMs (empty by default)
DEF_VMS: list[hv_types.VMInfo] = []

# Mutable test state (reset with clear() before each test)
TEMPLATES: list[hv_types.Template] = copy.deepcopy(DEF_TEMPLATES)
VERSIONS: list[hv_types.TemplateVersion] = copy.deepcopy(DEF_VERSIONS)
VMS: list[hv_types.VMInfo] = copy.deepcopy(DEF_VMS)


def clear() -> None:
    """
    Reset all templates, versions and VMs to their default state.
    Use this before each test to ensure a clean environment.
    """
    TEMPLATES[:] = copy.deepcopy(DEF_TEMPLATES)
    VERSIONS[:] = copy.deepcopy(DEF_VERSIONS)
    VMS[:] = copy.deepcopy(DEF_VMS)


def find_template(template_name: str) -> hv_types.Template:
    try:
        return next(t for t in TEMPLATES if t.name == template_name)
    except StopIteration:
        raise hv_exceptions.HarvesterNotFoundError(f'Template {template_name} not found') from None


def find_version(version_name: str) -> hv_types.TemplateVersion:
    try:
        return next(v for v in VERSIONS if v.name == version_name)
    except StopIteration:
        raise hv_exceptions.HarvesterNotFoundError(f'Template version {version_name} not found') from None


def replace_vm_info(vm_name: str, **kwargs: typing.Any) -> None:
    """
    Update attributes of a VM in VMS by name.
    Raises HarvesterNotFoundError if VM is not found.
    """
    try:
        vm = next(v for v in VMS if v.name == vm_name)
    except StopIteration:
        raise hv_exceptions.HarvesterNotFoundError(f'VM {vm_name} not found') from None
    for k, v in kwargs.items():
        setattr(vm, k, v)


def replacer_vm_info(**kwargs: typing.Any) -> Callable[..., None]:
    return functools.partial(replace_vm_info, **kwargs)


T_ = typing.TypeVar('T_')


def returner(value: T_, *args: typing.Any, **kwargs: typing.Any) -> Callable[..., T_]:
    def inner(*args: typing.Any, **kwargs: typing.Any) -> T_:
        return value

    return inner


# Provider values
PROVIDER_VALUES_DICT: gui.ValuesDictType = {
    'api_url': 'https://10.1.99.99',
    'api_token': 'test-token',
    'namespace': 'default',
    'verify_ssl': False,
    'concurrent_creation_limit': 1,
    'concurrent_removal_limit': 1,
    'timeout': 10,
}

# Connection identity matching PROVIDER_VALUES_DICT, as HarvesterClient.cache_key() would build it
CLIENT_CACHE_KEY = (
    f'{PROVIDER_VALUES_DICT["api_url"]}|{PROVIDER_VALUES_DICT["api_token"]}|'
    f'{PROVIDER_VALUES_DICT["namespace"]}'
)

# Service values
SERVICE_VALUES_DICT: gui.ValuesDictType = {
    'template': VERSIONS[0].name,
    'cores': 0,
    'memory': 0,
    'basename': 'base',
    'lenname': 4,
    'prov_uuid': '',
}


def create_client_mock() -> mock.Mock:
    """
    Create a MagicMock for HarvesterClient with default behaviors and side effects.
    Used to simulate API responses in provider/service tests.
    """
    client = mock.MagicMock()

    client.cache_key.return_value = CLIENT_CACHE_KEY
    client.test.return_value = True
    client.list_templates.return_value = copy.deepcopy(TEMPLATES)
    client.list_vms.return_value = [v.name for v in VMS]

    def get_template_side_effect(namespace: str, name: str, **kw: typing.Any) -> hv_types.Template:
        return copy.deepcopy(find_template(name))

    client.get_template.side_effect = get_template_side_effect

    def get_template_version_side_effect(*args: typing.Any, **kw: typing.Any) -> hv_types.TemplateVersion:
        version_ref = args[-1]
        return copy.deepcopy(find_version(version_ref.split('/')[-1]))

    client.get_template_version.side_effect = get_template_version_side_effect

    def create_template_version_side_effect(
        namespace: str, template_id: str, description: str, vm_spec: dict[str, typing.Any]
    ) -> hv_types.TemplateVersion:
        find_template(template_id.split('/')[-1])
        version = hv_types.TemplateVersion(
            name=f'uds-{uuid.uuid4().hex[:8]}',
            namespace=namespace,
            template_id=template_id,
            version=len(VERSIONS) + 1,
            ready=True,
            vm_spec=copy.deepcopy(vm_spec),
            description=description,
        )
        VERSIONS.append(copy.deepcopy(version))
        return copy.deepcopy(version)

    client.create_template_version.side_effect = create_template_version_side_effect

    def delete_template_version_side_effect(namespace: str, version_ref: str) -> None:
        version_name = version_ref.split('/')[-1]
        VERSIONS[:] = [v for v in VERSIONS if v.name != version_name]

    client.delete_template_version.side_effect = delete_template_version_side_effect

    def create_vm_side_effect(name: str, vm_spec: dict[str, typing.Any], running: bool = False, vm_metadata: dict[str, typing.Any] | None = None) -> None:
        interfaces = (
            [{'macAddress': '52:54:00:00:00:01', 'ipAddress': '10.1.250.99'}] if running else []
        )
        VMS.append(
            hv_types.VMInfo(name=name, namespace='default', running=running, exists=True, interfaces=interfaces)
        )

    client.create_vm.side_effect = create_vm_side_effect

    def delete_vm_side_effect(name: str, **kw: typing.Any) -> None:
        VMS[:] = [v for v in VMS if v.name != name]

    client.delete_vm.side_effect = delete_vm_side_effect

    def get_vm_info_side_effect(vm_name: str, **kw: typing.Any) -> hv_types.VMInfo:
        for vm in VMS:
            if vm.name == vm_name:
                return copy.deepcopy(vm)
        return hv_types.VMInfo.not_found(vm_name, 'default')

    client.get_vm_info.side_effect = get_vm_info_side_effect
    client.get_vm_interfaces.side_effect = lambda vm_name: get_vm_info_side_effect(vm_name).interfaces

    def set_vm_running_side_effect(name: str, running: bool) -> None:
        replace_vm_info(name, running=running)

    client.set_vm_running.side_effect = set_vm_running_side_effect

    return client


@contextlib.contextmanager
def patched_provider(**kwargs: typing.Any) -> Generator[provider.HarvesterProvider, None, None]:
    """
    Context manager that yields a provider with a patched HarvesterClient mock.
    Use this to ensure all API calls are intercepted and controlled in tests.
    """
    client = create_client_mock()
    prov = create_provider(**kwargs)
    prov._cached_api = client
    yield prov


def create_provider(**kwargs: typing.Any) -> provider.HarvesterProvider:
    """
    Create a HarvesterProvider instance with default or overridden values.
    """
    values = PROVIDER_VALUES_DICT.copy()
    values.update(kwargs)

    uuid_ = str(uuid.uuid4())
    return provider.HarvesterProvider(
        environment=environment.Environment.private_environment(uuid_), values=values, uuid=uuid_
    )


def create_service(
    provider: provider.HarvesterProvider | None = None, **kwargs: typing.Any
) -> service.HarvesterService:
    """
    Create a HarvesterService instance (dynamic service).
    """
    uuid_ = str(uuid.uuid4())
    values = SERVICE_VALUES_DICT.copy()
    values.update(kwargs)
    srvc = service.HarvesterService(
        environment=environment.Environment.private_environment(uuid_),
        provider=provider or create_provider(),
        values=values,
        uuid=uuid_,
    )
    return srvc


def create_publication(
    service: service.HarvesterService | None = None,
    api: mock.Mock | None = None,
    **kwargs: typing.Any,
) -> publication.HarvesterTemplatePublication:
    """
    Create a HarvesterTemplatePublication instance.
    When api is given, it replaces the api used by service (and by publication accesses).
    """
    uuid_ = str(uuid.uuid4())
    if service is None:
        service = create_service(**kwargs)
    if api is not None:
        service._cached_api = api
    pub = publication.HarvesterTemplatePublication(
        environment=environment.Environment.private_environment(uuid_),
        service=service,
        revision=1,
        servicepool_name='servicepool_name',
        uuid=uuid_,
    )
    pub._name = f'pub-{random.randint(1000, 9999)}'
    return pub


def create_userservice(
    service: service.HarvesterService | None = None,
    api: mock.Mock | None = None,
) -> deployment.HarvesterUserService:
    """
    Create a HarvesterUserService instance (dynamic user service), with its publication.
    service and the publication share it, so the same mock serves both.
    """
    uuid_ = str(uuid.uuid4())
    if service is None:
        service = create_service()
    if api is not None:
        service._cached_api = api
    pub = create_publication(service=service)
    usersrv = deployment.HarvesterUserService(
        environment=environment.Environment.private_environment(uuid_),
        service=service,
        publication=pub,
        uuid=uuid_,
    )
    if api is not None:
        usersrv._cached_api = api
    return usersrv
