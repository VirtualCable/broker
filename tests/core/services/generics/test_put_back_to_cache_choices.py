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

from uds.core.services.generics.dynamic.service import DynamicService
from uds.services.OpenShift.service import OpenshiftService
from uds.services.OpenShift.service_linked import OpenshiftLinkedService
from uds.services.OpenStack.service import OpenStackLiveService
from uds.services.Proxmox.service import ProxmoxService

from ....utils.test import UDSTestCase


def put_back_to_cache_choice_ids(service_type: type[DynamicService]) -> set[str]:
    field = service_type._gui_fields_template["put_back_to_cache"]
    return {str(choice.id) for choice in field.gui_description().choices or []}


class PutBackToCacheChoicesTest(UDSTestCase):
    def test_services_without_snapshot_support_do_not_offer_snapshot(self) -> None:
        for service_type in (OpenshiftService, OpenshiftLinkedService, OpenStackLiveService):
            with self.subTest(service=service_type.__name__):
                self.assertEqual(put_back_to_cache_choice_ids(service_type), {"no", "yes"})

    def test_services_with_snapshot_support_keep_snapshot(self) -> None:
        self.assertEqual(put_back_to_cache_choice_ids(ProxmoxService), {"no", "yes", "snapshot"})
