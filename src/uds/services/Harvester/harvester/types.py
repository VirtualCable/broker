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

import typing
from dataclasses import dataclass, field


@dataclass
class Template:
    """Harvester virtual machine template."""

    name: str
    namespace: str
    default_version_id: str = ""
    description: str = ""

    def is_usable(self) -> bool:
        """Template has a version UDS can instantiate from."""
        return bool(self.default_version_id)

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "namespace": self.namespace,
            "default_version_id": self.default_version_id,
            "description": self.description,
        }

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Template):
            return NotImplemented
        return self.as_dict() == other.as_dict()

    def __str__(self) -> str:
        return f"{self.namespace}/{self.name}"


@dataclass
class TemplateVersion:
    """Harvester virtual machine template version."""

    name: str
    namespace: str
    template_id: str = ""
    version: int = 0
    ready: bool = False
    vm_spec: dict[str, typing.Any] = field(default_factory=dict)
    vm_metadata: dict[str, typing.Any] = field(default_factory=dict)
    description: str = ""

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TemplateVersion):
            return NotImplemented
        return self.name == other.name and self.version == other.version

    def __str__(self) -> str:
        return f"{self.namespace}/{self.template_id} v{self.version}"


@dataclass
class VMInfo:
    """State of a kubevirt VirtualMachine (+ its running VMI when present)."""

    # printableStatus of the VMI, 'Running' when started
    name: str
    namespace: str
    running: bool = False
    exists: bool = True
    interfaces: list[dict[str, str]] = field(default_factory=list)

    @staticmethod
    def not_found(name: str, namespace: str) -> "VMInfo":
        return VMInfo(name=name, namespace=namespace, running=False, exists=False)

    @property
    def first_mac(self) -> str:
        for iface in self.interfaces:
            mac = iface.get("macAddress") or ""
            if mac:
                return mac
        return ""

    @property
    def first_ip(self) -> str:
        for iface in self.interfaces:
            ip = iface.get("ipAddress") or ""
            if ip:
                return ip
        return ""

    def is_usable(self) -> bool:
        """Usable by UDS: exists and (running or stopped without error)."""
        return self.exists

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VMInfo):
            return NotImplemented
        return self.name == other.name and self.namespace == other.namespace

    def __str__(self) -> str:
        return f"{self.namespace}/{self.name} (running={self.running})"
