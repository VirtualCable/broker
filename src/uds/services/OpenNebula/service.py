#
# Copyright (c) 2012-2019 Virtual Cable S.L.
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
Author: Andres Schumann, aschumann at virtualcable dot es
"""

import logging
import typing

from django.utils.translation import gettext_noop as _

from uds.core import exceptions, types
from uds.core.services.generics.dynamic.service import DynamicService
from uds.core.ui import gui
from uds.core.util import validators

from . import on
from .deployment import OpenNebulaLiveDeployment
from .publication import OpenNebulaLivePublication

# Not imported at runtime, just for type checking
if typing.TYPE_CHECKING:
    from uds.core.services.generics.dynamic.publication import DynamicPublication
    from uds.core.services.generics.dynamic.userservice import DynamicUserService

    from .provider import OpenNebulaProvider

logger: logging.Logger = logging.getLogger(__name__)

# Kinds of items that can be removed by a deferred deletion, stored as "<kind>:<id>"
VM_KIND: typing.Final[str] = "VM"
TEMPLATE_KIND: typing.Final[str] = "TP"


class OpenNebulaLiveService(DynamicService):
    """
    Opennebula Live Service
    """

    # : Name to show the administrator. This string will be translated BEFORE
    # : sending it to administration interface, so don't forget to
    # : mark it as _ (using gettext_noop)
    type_name = _("OpenNebula Live Images")
    # : Type used internally to identify this provider
    type_type = "openNebulaLiveService"
    # : Description shown at administration interface for this provider
    type_description = _("OpenNebula live images based service")
    # : Icon file used as icon for this provider. This string will be translated
    # : BEFORE sending it to administration interface, so don't forget to
    # : mark it as _ (using gettext_noop)
    icon_file = "provider.png"

    # Functional related data

    # : If we need to generate "cache" for this service, so users can access the
    # : provided services faster. Is uses_cache is True, you will need also
    # : set publication_type, do take care about that!
    uses_cache = True
    # : Tooltip shown to user when this item is pointed at admin interface, none
    # : because we don't use it
    cache_tooltip = _("Number of desired machines to keep running waiting for an user")
    # : If we need to generate a "Level 2" cache for this service (i.e., L1
    # : could be running machines and L2 suspended machines)
    uses_cache_l2 = True
    # : Tooltip shown to user when this item is pointed at admin interface, None
    # : also because we don't use it
    cache_tooltip_l2 = _("Number of desired VMs to keep stopped waiting for use")

    # : If the service needs a s.o. manager (managers are related to agents
    # : provided by services itselfs, i.e. virtual machines with actors)
    needs_osmanager = True
    can_reset = True

    # OpenNebula removes the machines by itself, whatever their power state is
    must_stop_before_deletion = False

    # : Types of publications (preparated data for deploys)
    # : In our case, we do no need a publication, so this is None
    publication_type = OpenNebulaLivePublication
    # : Types of deploys (services in cache and/or assigned to users)
    user_service_type = OpenNebulaLiveDeployment

    allowed_protocols = types.transports.Protocol.generic_vdi(types.transports.Protocol.SPICE)
    services_type_provided = types.services.ServiceType.VDI

    # Now the form part
    datastore = gui.ChoiceField(
        label=_("Datastore"),
        order=100,
        tooltip=_("Service clones datastore"),
        required=True,
    )

    template = gui.ChoiceField(
        label=_("Base Template"),
        order=110,
        tooltip=_("Service base template"),
        tab=types.ui.Tab.MACHINE,
        required=True,
    )

    basename = DynamicService.basename
    lenname = DynamicService.lenname

    @typing.override
    def initialize(self, values: "types.core.ValuesType") -> None:
        """
        We check here form values to see if they are valid.

        Note that we check them throught FROM variables, that already has been
        initialized by __init__ method of base class, before invoking this.
        """
        if not values:
            return

        self.basename.value = validators.validate_basename(self.basename.value, length=self.lenname.as_int())

    @typing.override
    def provider(self) -> "OpenNebulaProvider":
        return typing.cast("OpenNebulaProvider", super().provider())

    @typing.override
    def init_gui(self) -> None:
        """
        Loads required values inside
        """

        self.template.set_choices([gui.choice_item(t.id, t.name) for t in self.provider().get_templates()])

        self.datastore.set_choices([gui.choice_item(d.id, d.name) for d in self.provider().get_datastores()])

    @typing.override
    def sanitized_name(self, name: str) -> str:
        return self.provider().sanitized_name(name)

    def make_template(self, name: str) -> str:
        return self.provider().make_template(self.template.value, name, self.datastore.value)

    def check_template_published(self, template_id: str) -> bool:
        return self.provider().check_template_published(template_id)

    def deploy_from_template(self, name: str, template_id: str) -> str:
        """
        Deploys a virtual machine on selected cluster from selected template

        Args:
            name: Name (sanitized) of the machine
            template_id: Id of the template to deploy from

        Returns:
            Id of the machine being created form template
        """
        logger.debug("Deploying from template %s machine %s", template_id, name)
        return self.provider().deply_from_template(name, template_id)

    def get_machine_state(self, machine_id: str) -> "on.types.VmState":
        """
        Invokes getMachineState from parent provider
        Returns UNKNOWN if the machine is not known
        """
        return self.provider().get_machine_state(machine_id)

    def get_machine_substate(self, machine_id: str) -> int:
        """
        On OpenNebula, the machine can be "active" but not "running".
        Any active machine will have a LCM_STATE, that is what we get here
        """
        return self.provider().get_machine_substate(machine_id)

    def get_network_info(self, machine_id: str, network_id: str | None = None) -> tuple[str, str]:
        """
        Gets the (mac, ip) of a machine
        """
        return self.provider().get_network_info(machine_id, network_id=network_id)

    @typing.override
    def get_ip(self, caller_instance: "DynamicUserService | DynamicPublication | None", vmid: str) -> str:
        return self.get_network_info(vmid)[1]

    @typing.override
    def get_mac(
        self,
        caller_instance: "DynamicUserService | DynamicPublication | None",
        vmid: str,
        *,
        for_unique_id: bool = False,
    ) -> str:
        # The mac is assigned by OpenNebula, so there is nothing to give before the machine exists
        if for_unique_id or not vmid:
            return ""
        return self.get_network_info(vmid)[0].upper()

    @typing.override
    def is_running(self, caller_instance: "DynamicUserService | DynamicPublication | None", vmid: str) -> bool:
        return self.get_machine_state(vmid) == on.types.VmState.ACTIVE

    @typing.override
    def start(self, caller_instance: "DynamicUserService | DynamicPublication | None", vmid: str) -> None:
        # This start also "resumes" suspended/paused machines
        self.provider().start_machine(vmid)

    @typing.override
    def stop(self, caller_instance: "DynamicUserService | DynamicPublication | None", vmid: str) -> None:
        self.provider().stop_machine(vmid)

    @typing.override
    def shutdown(self, caller_instance: "DynamicUserService | DynamicPublication | None", vmid: str) -> None:
        self.provider().shutdown_machine(vmid)

    @typing.override
    def reset(self, caller_instance: "DynamicUserService | DynamicPublication | None", vmid: str) -> None:
        self.provider().reset_machine(vmid)

    @typing.override
    def delete(self, caller_instance: "DynamicUserService | DynamicPublication | None", vmid: str) -> None:
        # Publications are templates and user services are machines, so the deletion differs
        kind = TEMPLATE_KIND if isinstance(caller_instance, OpenNebulaLivePublication) else VM_KIND
        super().delete(caller_instance, f"{kind}:{vmid}")

    @typing.override
    def execute_delete(self, vmid: str) -> None:
        kind, _sep, item_id = vmid.partition(":")
        if kind == TEMPLATE_KIND:
            self.provider().remove_template(item_id)
            return

        state = self.get_machine_state(item_id)
        if state == on.types.VmState.UNKNOWN:
            raise exceptions.services.generics.NotFoundError(f"Machine {item_id} not found")

        if state == on.types.VmState.ACTIVE:
            sub_state = self.get_machine_substate(item_id)
            if sub_state < 3:  # Less than running
                raise exceptions.services.generics.RetryableError(
                    f"Machine {item_id} must be running before removing it (substate {sub_state})"
                )

        self.provider().remove_machine(item_id)

    def get_console_connection(self, vmid: str) -> types.services.ConsoleConnectionInfo | None:
        return self.provider().get_console_connection(vmid)

    def desktop_login(
        self, vmid: str, username: str, password: str, domain: str
    ) -> types.services.ConsoleConnectionInfo | None:
        return self.provider().desktop_login(vmid, username, password, domain)

    @typing.override
    def is_available(self) -> bool:
        return self.provider().is_available()
