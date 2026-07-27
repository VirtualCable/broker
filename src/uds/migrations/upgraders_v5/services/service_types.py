# -*- coding: utf-8 -*-
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
Author: Adolfo Gomez, dkmaster at dkmon dot com
"""

import dataclasses
import typing


@dataclasses.dataclass(frozen=True)
class ServiceTypeInfo:
    us: bool = True
    pub: bool = False


@dataclasses.dataclass(frozen=True)
class ProviderTypeInfo:
    prov: bool = True
    svc: bool = False


SERVICE_TYPES: typing.Final[dict[str, ServiceTypeInfo]] = {
    "XenLinkedService": ServiceTypeInfo(us=True, pub=True),
    "ProxmoxLinkedService": ServiceTypeInfo(us=True, pub=True),
    "oVirtLinkedService": ServiceTypeInfo(us=True, pub=True),
    "openStackLiveService": ServiceTypeInfo(us=True, pub=True),
    "openNebulaLiveService": ServiceTypeInfo(us=True, pub=True),
    "openGnsysMachine": ServiceTypeInfo(us=True, pub=False),
    "NutanixService": ServiceTypeInfo(us=True, pub=True),
    "PrismCentralService": ServiceTypeInfo(us=True, pub=True),
    "HyperVLinkedServiceNew": ServiceTypeInfo(us=True, pub=True),
    "HyperVLinkedServiceNewGen2": ServiceTypeInfo(us=True, pub=True),
    "RemoteAppService": ServiceTypeInfo(us=True, pub=False),
    "RemoteSessionService": ServiceTypeInfo(us=True, pub=False),
    "VCloudVapp": ServiceTypeInfo(us=True, pub=True),
    "AWSEAMI": ServiceTypeInfo(us=True, pub=True),
    "AzureVm": ServiceTypeInfo(us=True, pub=True),
    "IPSingleMachineService": ServiceTypeInfo(us=True, pub=False),
    "VCLinkedCloneService": ServiceTypeInfo(us=True, pub=True),
    "VCFullCloneService": ServiceTypeInfo(us=True, pub=True),
    "VCFixedMachinesService": ServiceTypeInfo(us=True, pub=False),
}

# Only provider/service types with unmarshal + mark_for_upgrade in their source
PROVIDER_TYPES: typing.Final[dict[str, ProviderTypeInfo]] = {
    "VmwareVCServiceProvider": ProviderTypeInfo(prov=True, svc=False),
    "VCLinkedCloneService": ProviderTypeInfo(prov=False, svc=True),
}
