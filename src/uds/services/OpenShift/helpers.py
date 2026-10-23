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

from django.utils.translation import gettext as _

from uds.core import types
from uds.core.ui.user_interface import gui

from .openshift import types as oshift_types

if typing.TYPE_CHECKING:
    from .openshift.client import OpenshiftClient


def _same_as_template() -> types.ui.ChoiceItem:
    return gui.choice_item("", _("Same as template"))


def machine_choices(api: "OpenshiftClient") -> types.ui.CallbackResultType:
    """
    Storage class, instance type and network choices for the cloned VMs.
    The first choice of every list keeps the template value.
    """
    storage_classes = [_same_as_template()] + [
        gui.choice_item(sc.name, _("{} (default)").format(sc.name) if sc.is_default else sc.name)
        for sc in api.list_storage_classes()
    ]
    instance_types = [_same_as_template()] + [
        gui.choice_item(it.name, _("{} ({} CPU, {})").format(it.name, it.cpus, it.memory))
        for it in api.list_instance_types()
    ]
    networks = [
        _same_as_template(),
        gui.choice_item(oshift_types.POD_NETWORK, _("Pod network")),
    ] + [gui.choice_item(net, net) for net in api.list_networks()]
    return [
        {"name": "storage_class", "choices": storage_classes},
        {"name": "instance_type", "choices": instance_types},
        {"name": "network", "choices": networks},
    ]
