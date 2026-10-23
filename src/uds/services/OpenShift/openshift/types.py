import collections.abc
import dataclasses
import enum
import logging
import typing

from . import exceptions

logger: logging.Logger = logging.getLogger(__name__)


class State(enum.StrEnum):
    """
    Represents the state of a Openshift vm.
    """

    # Phases
    PENDING = "Pending"
    SCHEDULING = "Scheduling"
    SCHEDULED = "Scheduled"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    UNKNOWN = "Unknown"

    # Statuses (printableStatus)
    STARTING = "Starting"
    STOPPED = "Stopped"
    STOPPING = "Stopping"
    MIGRATING = "Migrating"
    PAUSED = "Paused"
    SUSPENDED = "Suspended"
    RESTORING = "Restoring"
    CLONING = "Cloning"
    ERROR = "Error"
    CRASH_LOOP_BACK_OFF = "CrashLoopBackOff"
    WAITING_FOR_VMI = "WaitingForVMI"
    WAITING_FOR_VOLUME_BIND = "WaitingForVolumeBind"
    WAITING_FOR_NETWORK = "WaitingForNetwork"
    WAITING_FOR_LAUNCHER_POD = "WaitingForLauncherPod"
    WAITING_FOR_IMAGE = "WaitingForImage"
    WAITING_FOR_DATA_VOLUME = "WaitingForDataVolume"
    WAITING_FOR_USER_DATA = "WaitingForUserData"
    WAITING_FOR_CLOUD_INIT = "WaitingForCloudInit"
    WAITING_FOR_GUEST_AGENT = "WaitingForGuestAgent"
    DELETING = "Deleting"
    TERMINATING = "Terminating"
    # Custom/legacy
    NOT_USABLE = "notUsable"
    DENIED = "Denied"
    WARNING = "Warning"

    # Not usable, own state
    # We do not support multinode instances, and will not support them because it's a nonsense for UDS
    # but we will find for sure. We also do not support emtpy instances, so we will not use them

    def is_succeeded(self) -> bool:
        """
        Check if the instance is in a successful state.
        """
        return self == State.SUCCEEDED

    def is_cloneable(self) -> bool:
        """
        Check if the instance is in a state that allows cloning.
        """
        return self in {
            State.RUNNING,
            State.STOPPED,
            State.SUSPENDED,
        }

    def is_cloning(self) -> bool:
        """
        Check if the instance is currently being cloned.
        """
        return self == State.CLONING

    def is_running(self) -> bool:
        """
        Check if the instance is running.
        """
        return self == State.RUNNING

    def is_provisioning(self) -> bool:
        """
        Check if the instance is currently being provisioned.
        """
        # OpenShift does not have a 'PROVISIONING' status; adjust as needed
        return self in {
            State.PENDING,
            State.SCHEDULING,
            State.SCHEDULED,
            State.STARTING,
            State.WAITING_FOR_VMI,
            State.WAITING_FOR_VOLUME_BIND,
            State.WAITING_FOR_NETWORK,
            State.WAITING_FOR_LAUNCHER_POD,
            State.WAITING_FOR_IMAGE,
            State.WAITING_FOR_DATA_VOLUME,
            State.WAITING_FOR_USER_DATA,
            State.WAITING_FOR_CLOUD_INIT,
            State.WAITING_FOR_GUEST_AGENT,
            State.RESTORING,
            State.CLONING,
            State.MIGRATING,
        }

    def is_stopped(self) -> bool:
        """
        Check if the instance is stopped.
        """
        return self == State.STOPPED

    def is_off(self) -> bool:
        """
        Check if the instance is off (stopped or suspended).
        """
        return self in {State.STOPPED, State.SUSPENDED}

    def is_error(self) -> bool:
        """
        Check if the instance is in an error state.
        """
        return self in {
            State.FAILED,
            State.DENIED,
        }

    def is_usable(self) -> bool:
        """
        Check if the instance is usable.
        """
        return self not in (State.NOT_USABLE, State.UNKNOWN)

    @staticmethod
    def from_string(state: str) -> "State":
        """
        Convert a string to a OpenshiftState.
        """
        try:
            return State(state)
        except ValueError:
            logger.warning(f"Unknown instance state: {state}")
            return State.UNKNOWN


@dataclasses.dataclass
class Interface:
    name: str
    mac_address: str
    ip_address: str

    @staticmethod
    def from_dict(dictionary: collections.abc.MutableMapping[str, typing.Any]) -> "Interface":
        try:
            return Interface(
                name=dictionary.get("interfaceName", ""),
                mac_address=dictionary.get("mac", ""),
                ip_address=dictionary.get("ipAddress", ""),
            )
        except Exception as e:
            logger.error(f"Error creating Interface from dict: {e}")
            raise exceptions.OpenshiftError("Invalid Interface data") from e


@dataclasses.dataclass
class VolumeTemplate:
    name: str
    storage: str

    @staticmethod
    def from_dict(dictionary: collections.abc.MutableMapping[str, typing.Any]) -> "VolumeTemplate":
        try:
            meta = dictionary.get("metadata", {})
            spec = dictionary.get("spec", {})
            storage = spec.get("storage", {})
            resources = storage.get("resources", {})
            requests = resources.get("requests", {})
            return VolumeTemplate(name=meta.get("name", ""), storage=requests.get("storage", ""))
        except Exception as e:
            logger.error(f"Error creating VolumeTemplate from dict: {e}")
            raise exceptions.OpenshiftError("Invalid VolumeTemplate data") from e

    @staticmethod
    def none() -> "VolumeTemplate":
        return VolumeTemplate(name="", storage="")


@dataclasses.dataclass
class DeviceDisk:
    name: str
    boot_order: int

    @staticmethod
    def from_dict(dictionary: collections.abc.MutableMapping[str, typing.Any]) -> "DeviceDisk":
        try:
            return DeviceDisk(
                name=dictionary.get("name", ""),
                boot_order=dictionary.get("bootOrder", 0),
            )
        except Exception as e:
            logger.error(f"Error creating DeviceDisk from dict: {e}")
            raise exceptions.OpenshiftError("Invalid DeviceDisk data") from e


@dataclasses.dataclass
class Volume:
    name: str
    data_volume: str

    @staticmethod
    def from_dict(dictionary: collections.abc.MutableMapping[str, typing.Any]) -> "Volume":
        try:
            dv = dictionary.get("dataVolume", {})
            return Volume(
                name=dictionary.get("name", ""),
                data_volume=dv.get("name", ""),
            )
        except Exception as e:
            logger.error(f"Error creating Volume from dict: {e}")
            raise exceptions.OpenshiftError("Invalid Volume data") from e


@dataclasses.dataclass
class VM:
    name: str
    namespace: str
    uid: str
    status: State
    volume_template: VolumeTemplate
    disks: list[DeviceDisk]
    volumes: list[Volume]
    # phase: State = State.UNKNOWN  # Use InstanceStatus for phase

    def validate(self) -> "VM":
        if not self.is_usable():
            raise exceptions.OpenshiftError(f"VM {self.name} is not usable (status: {self.status})")

        return self

    def is_usable(self) -> bool:
        return self.status.is_usable()

    def is_running(self) -> bool:
        return self.status.is_running()

    @staticmethod
    def from_dict(dictionary: collections.abc.MutableMapping[str, typing.Any]) -> "VM":
        try:
            metadata = dictionary.get("metadata", {})
            status = dictionary.get("status", {}).get("printableStatus", "UNKNOWN")
            spec: dict[str, typing.Any] = dictionary.get("spec", {})
            template = spec.get("template", {}).get("spec", {})
            return VM(
                name=metadata.get("name", ""),
                namespace=metadata.get("namespace", ""),
                uid=metadata.get("uid", ""),
                status=State.from_string(status),
                volume_template=VolumeTemplate.from_dict(spec.get("dataVolumeTemplates", [{}])[0]),
                disks=[DeviceDisk.from_dict(disk) for disk in template.get("devices", {}).get("disks", [])],
                volumes=[Volume.from_dict(vol) for vol in template.get("volumes", [])],
                # phase=State.from_string(phase_str) if phase_str else State.UNKNOWN,
            )
        except Exception as e:
            logger.error(f"Error creating Definition from dict: {e}")
            raise exceptions.OpenshiftError("Invalid Definition data") from e


POD_NETWORK: typing.Final[str] = "pod"


@dataclasses.dataclass(frozen=True)
class StorageClass:
    name: str
    is_default: bool


@dataclasses.dataclass(frozen=True)
class InstanceType:
    name: str
    cpus: int
    memory: str


@dataclasses.dataclass(frozen=True)
class MachineOptions:
    """
    Per-service overrides for the VMs cloned from the template.
    Empty or zero values keep what the source VM has.
    """

    storage_class: str = ""
    instance_type: str = ""
    cores: int = 0
    memory_mb: int = 0
    network: str = ""  # POD_NETWORK, "namespace/name" of a NetworkAttachmentDefinition, or "" to keep

    def apply_to_vm(self, vm_obj: dict[str, typing.Any]) -> None:
        template_spec = vm_obj["spec"]["template"]["spec"]
        domain = template_spec.setdefault("domain", {})

        if self.instance_type:
            # KubeVirt rejects VMs that set guest cpu/memory together with an instance type
            vm_obj["spec"]["instancetype"] = {"kind": "VirtualMachineClusterInstancetype", "name": self.instance_type}
            domain.pop("cpu", None)
            domain.pop("memory", None)
            domain.get("resources", {}).get("requests", {}).pop("memory", None)
        else:
            if (self.cores or self.memory_mb) and vm_obj["spec"].get("instancetype"):
                raise exceptions.OpenshiftError(
                    f"VM {vm_obj['metadata'].get('name', '')} uses an instance type: "
                    "choose an instance type in the service instead of cores or memory"
                )
            if self.cores:
                domain.setdefault("cpu", {})["cores"] = self.cores
            if self.memory_mb:
                memory = f"{self.memory_mb}Mi"
                domain.setdefault("memory", {})["guest"] = memory
                requests = domain.get("resources", {}).get("requests", {})
                if "memory" in requests:
                    requests["memory"] = memory

        if self.network:
            self._apply_network(template_spec, domain)

    def _apply_network(self, template_spec: dict[str, typing.Any], domain: dict[str, typing.Any]) -> None:
        interfaces = domain.setdefault("devices", {}).setdefault("interfaces", [])
        networks = template_spec.setdefault("networks", [])
        name = interfaces[0]["name"] if interfaces else (networks[0]["name"] if networks else "default")

        if self.network == POD_NETWORK:
            network: dict[str, typing.Any] = {"name": name, "pod": {}}
            interface: dict[str, typing.Any] = {"name": name, "masquerade": {}}
        else:
            # Secondary (multus) networks use bridge binding; masquerade only works on the pod network
            network = {"name": name, "multus": {"networkName": self.network}}
            interface = {"name": name, "bridge": {}}

        if interfaces:
            for binding in ("masquerade", "bridge", "sriov", "passt", "binding", "macAddress"):
                interfaces[0].pop(binding, None)
            interfaces[0].update(interface)
        else:
            interfaces.append(interface)

        replaced = False
        for index, current in enumerate(networks):
            if current.get("name") == name:
                networks[index] = network
                replaced = True
        if not replaced:
            networks.insert(0, network)
