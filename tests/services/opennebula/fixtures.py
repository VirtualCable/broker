"""
Author: Andres Schumann, aschumann at virtualcable dot es
"""

import collections.abc
import contextlib
import dataclasses
import typing
import uuid

from unittest import mock

from uds.core import environment
from uds.core.ui import gui
from uds.services.OpenNebula import deployment, on, provider, publication, service

PROVIDER_VALUES_DICT: typing.Final[gui.ValuesDictType] = {
    "host": "host",
    "port": 2633,
    "ssl": False,
    "username": "oneadmin",
    "password": "password",
    "concurrent_creation_limit": 1,
    "concurrent_removal_limit": 1,
    "timeout": 10,
    "verify_ssl": False,
}

SERVICE_VALUES_DICT: typing.Final[gui.ValuesDictType] = {
    "datastore": "1",
    "template": "10",
    "basename": "bname",
    "lenname": 5,
}

# lcm state of an active machine that is already running
LCM_RUNNING: typing.Final[int] = 3


@dataclasses.dataclass
class FakeVm:
    state: on.types.VmState = on.types.VmState.ACTIVE
    substate: int = LCM_RUNNING
    mac: str = "02:00:c0:a8:00:01"
    ip: str = "192.168.0.1"


class FakeOpenNebula:
    """
    Keeps the machines and templates of an OpenNebula server, so the real code of the provider, the service,
    the publication and the user service can be exercised
    """

    vms: dict[str, FakeVm]
    templates: dict[str, bool]  # id -> published
    names: dict[str, str]  # id -> name
    deploy_options: dict[str, on.types.MachineOptions]  # machine id -> options it was deployed with
    calls: list[tuple[str, str]]
    _next_id: int

    def __init__(self) -> None:
        self.vms = {}
        self.templates = {}
        self.names = {}
        self.deploy_options = {}
        self.calls = []
        self._next_id = 100

    def _new_id(self) -> str:
        self._next_id += 1
        return str(self._next_id)

    def called(self, what: str) -> list[str]:
        return [i for w, i in self.calls if w == what]

    # on.vm
    def get_machine_state(self, api: typing.Any, vmid: str) -> on.types.VmState:
        return self.vms[vmid].state if vmid in self.vms else on.types.VmState.UNKNOWN

    def get_machine_substate(self, api: typing.Any, vmid: str) -> int:
        return self.vms[vmid].substate

    def start_machine(self, api: typing.Any, vmid: str) -> None:
        self.calls.append(("start", vmid))
        self.vms[vmid].state = on.types.VmState.ACTIVE

    def stop_machine(self, api: typing.Any, vmid: str) -> None:
        self.calls.append(("stop", vmid))
        self.vms[vmid].state = on.types.VmState.STOPPED

    def shutdown_machine(self, api: typing.Any, vmid: str) -> None:
        self.calls.append(("shutdown", vmid))
        self.vms[vmid].state = on.types.VmState.POWEROFF

    def reset_machine(self, api: typing.Any, vmid: str) -> None:
        self.calls.append(("reset", vmid))

    def remove_machine(self, api: typing.Any, vmid: str) -> None:
        self.calls.append(("remove", vmid))
        del self.vms[vmid]

    def get_network_info(self, api: typing.Any, vmid: str, network_id: str | None = None) -> tuple[str, str]:
        vm = self.vms[vmid]
        return vm.mac, vm.ip

    # on.template
    def template_create(self, api: typing.Any, from_template_id: str, name: str, datastore: str) -> str:
        template_id = self._new_id()
        self.templates[template_id] = False
        self.names[template_id] = name
        self.calls.append(("make_template", template_id))
        return template_id

    def template_check_published(self, api: typing.Any, template_id: str) -> bool:
        # Needs one check to be published
        published = self.templates[template_id]
        self.templates[template_id] = True
        return published

    def template_remove(self, api: typing.Any, template_id: str) -> None:
        self.calls.append(("remove_template", template_id))
        del self.templates[template_id]

    def template_deploy_from(
        self,
        api: typing.Any,
        template_id: str,
        name: str,
        options: on.types.MachineOptions,
    ) -> str:
        vmid = self._new_id()
        self.vms[vmid] = FakeVm()
        self.names[vmid] = name
        self.deploy_options[vmid] = options
        self.calls.append(("deploy", vmid))
        return vmid


@contextlib.contextmanager
def patched_opennebula() -> collections.abc.Generator[FakeOpenNebula]:
    fake = FakeOpenNebula()
    with contextlib.ExitStack() as stack:
        for module, name, method in (
            (on.vm, "get_machine_state", fake.get_machine_state),
            (on.vm, "get_machine_substate", fake.get_machine_substate),
            (on.vm, "start_machine", fake.start_machine),
            (on.vm, "stop_machine", fake.stop_machine),
            (on.vm, "shutdown_machine", fake.shutdown_machine),
            (on.vm, "reset_machine", fake.reset_machine),
            (on.vm, "remove_machine", fake.remove_machine),
            (on.vm, "get_network_info", fake.get_network_info),
            (on.template, "create", fake.template_create),
            (on.template, "check_published", fake.template_check_published),
            (on.template, "remove", fake.template_remove),
            (on.template, "deploy_from", fake.template_deploy_from),
        ):
            stack.enter_context(mock.patch.object(module, name, method))
        yield fake


def create_provider(**kwargs: typing.Any) -> provider.OpenNebulaProvider:
    values = PROVIDER_VALUES_DICT.copy()
    values.update(kwargs)

    uuid_ = str(uuid.uuid4())
    return provider.OpenNebulaProvider(
        environment=environment.Environment.private_environment(uuid_), values=values, uuid=uuid_
    )


def create_service(
    provider: provider.OpenNebulaProvider, **kwargs: typing.Any
) -> service.OpenNebulaLiveService:
    values = SERVICE_VALUES_DICT.copy()
    values.update(kwargs)

    uuid_ = str(uuid.uuid4())
    srvc = service.OpenNebulaLiveService(
        provider=provider,
        environment=environment.Environment.private_environment(uuid_),
        values=values,
        uuid=uuid_,
    )
    service_db_mock = mock.MagicMock()
    service_db_mock.uuid = uuid_
    service_db_mock.name = "ServiceName"
    srvc.db_obj = mock.MagicMock()
    srvc.db_obj.return_value = service_db_mock
    return srvc


def create_publication(srvc: service.OpenNebulaLiveService) -> publication.OpenNebulaLivePublication:
    uuid_ = str(uuid.uuid4())
    return publication.OpenNebulaLivePublication(
        environment=environment.Environment.private_environment(uuid_),
        service=srvc,
        revision=1,
        servicepool_name="servicepool_name",
        uuid=uuid_,
    )


def create_userservice(
    srvc: service.OpenNebulaLiveService,
    pub: publication.OpenNebulaLivePublication | None = None,
) -> deployment.OpenNebulaLiveDeployment:
    uuid_ = str(uuid.uuid4())
    return deployment.OpenNebulaLiveDeployment(
        environment=environment.Environment.private_environment(uuid_),
        service=srvc,
        publication=pub or create_publication(srvc),
        uuid=uuid_,
    )
