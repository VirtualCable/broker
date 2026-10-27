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

import typing

from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from uds.core import exceptions, types
from uds.core.ui import gui

from . import helpers
from .deployment_linked import OpenshiftLinkedUserService
from .openshift import types as oshift_types
from .service import OpenshiftService


class OpenshiftLinkedService(OpenshiftService):
    """
    Linked clones of the publication VM.

    - Persistent: each VM gets its own DataVolume cloned from the publication disk. Only offered when
      the storage of the template is copy-on-write, so the clone shares blocks instead of copying them.
    - Non persistent: each VM runs on an ephemeral copy-on-write layer over the publication disk, with no
      clone at all. The layer survives reboots from the guest, but is discarded when the VM is stopped.
    """

    type_name = _("VM linked clone")
    type_type = "OpenshiftLinkedService"
    type_description = _("This service provides access to linked clones of a VM on Openshift")
    icon_file = "service.png"

    # L2 cache keeps machines stopped, and a non persistent machine loses its disk when stopped
    uses_cache_l2 = False

    user_service_type = OpenshiftLinkedUserService

    template = gui.ChoiceField(
        order=3,
        label=_("Template VM"),
        tooltip=_("Template to use for VMs. Its storage decides if persistent linked clones are available."),
        fills={
            "callback_name": "openshiftFillLinkedModes",
            "function": helpers.get_linked_mode_choices,
            "parameters": ["template"],
        },
        required=True,
    )
    mode = gui.ChoiceField(
        order=6,
        label=_("Mode"),
        tooltip=_(
            "Persistent: each VM keeps its own disk, cloned sharing blocks with the publication "
            "(needs a copy-on-write storage such as Ceph RBD/OpenShift Data Foundation). "
            "Non persistent: VMs run over the publication disk and are reset to it whenever they are stopped."
        ),
        default=oshift_types.LinkedCloneMode.NON_PERSISTENT,
        required=True,
    )

    basename = OpenshiftService.basename
    lenname = OpenshiftService.lenname

    try_soft_shutdown = OpenshiftService.try_soft_shutdown
    maintain_on_error = OpenshiftService.maintain_on_error
    put_back_to_cache = OpenshiftService.put_back_to_cache

    storage_class = OpenshiftService.storage_class
    instance_type = OpenshiftService.instance_type
    cores = OpenshiftService.cores
    memory = OpenshiftService.memory
    network = OpenshiftService.network

    prov_uuid = OpenshiftService.prov_uuid

    @typing.override
    def initialize(self, values: "types.core.ValuesType") -> None:
        super().initialize(values)
        if not values:
            return

        if self.storage_class.value:
            raise exceptions.ui.ValidationError(
                gettext("Linked clones stay on the storage of the template: leave Storage Class empty")
            )

        if self.is_persistent():
            storage = self.api.get_vm_storage_info(self.template.value)
            if not storage.copy_on_write:
                raise exceptions.ui.ValidationError(
                    gettext(
                        "Storage {} of the template makes full copies: persistent linked clones are not available, "
                        "use non persistent mode or the VM clone service"
                    ).format(storage.storage_class or gettext("unknown"))
                )

    @typing.override
    def init_gui(self) -> None:
        super().init_gui()
        self.template.set_cb_ticket("prov_uuid", self.provider().get_uuid())
        self.mode.set_choices(
            [
                gui.choice_item(oshift_types.LinkedCloneMode.PERSISTENT, _("Persistent")),
                gui.choice_item(oshift_types.LinkedCloneMode.NON_PERSISTENT, _("Non persistent (reset when stopped)")),
            ]
        )

    def is_persistent(self) -> bool:
        return self.mode.value == oshift_types.LinkedCloneMode.PERSISTENT
