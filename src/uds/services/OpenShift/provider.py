#
# Copyright (c) 2025-2026 Virtual Cable S.L.
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
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PAdecorators.FTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TOdecorators.FT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import logging
import re
import typing

from django.utils.translation import gettext_noop as _

from uds.core import consts
from uds.core import types as core_types
from uds.core.services import ServiceProvider
from uds.core.ui import gui
from uds.core.util import fields
from uds.core.util.decorators import cached

from .openshift import client
from .service import OpenshiftService
from .service_fixed import OpenshiftServiceFixed

# Not imported at runtime, just for type checking
if typing.TYPE_CHECKING:
    from uds.core import environment

logger = logging.getLogger(__name__)


class OpenshiftProvider(ServiceProvider):
    offers = [OpenshiftService, OpenshiftServiceFixed]
    type_name = _("Openshift Provider")
    type_type = "OpenshiftProvider"
    type_description = _("Openshift based VMs provider")
    icon_file = "provider.png"

    # Gui
    cluster_url = gui.TextField(
        order=1,
        length=128,
        label=_("Cluster OAuth URL"),
        tooltip=_(
            "Openshift OAuth URL, e.g. https://oauth-openshift.apps-crc.testing or https://console-openshift.apps-crc.testing"
        ),
        required=True,
        default="",
    )
    api_url = gui.TextField(
        order=2,
        length=128,
        label=_("API URL"),
        tooltip=_("Openshift API URL, e.g. https://api.crc.testing:6443"),
        required=True,
        default="",
    )
    username = gui.TextField(
        order=3,
        length=64,
        label=_("Username"),
        tooltip=_("User with valid privileges on Openshift Server"),
        required=True,
        default="kubeadmin",
    )
    password = gui.PasswordField(
        order=4,
        length=64,
        label=_("Password"),
        tooltip=_("Password of the user of Openshift Server"),
        required=True,
        default="",
    )
    namespace = gui.TextField(
        order=5,
        length=64,
        label=_("Namespace"),
        tooltip=_('Openshift namespace to use (default: "default")'),
        required=True,
        default="default",
    )
    verify_ssl = fields.verify_ssl_field(order=6)
    concurrent_creation_limit = fields.concurrent_creation_limit_field()
    concurrent_removal_limit = fields.concurrent_removal_limit_field()
    timeout = fields.timeout_field()

    _cached_api: "client.OpenshiftClient | None" = None  # Cached API client

    @typing.override
    def initialize(self, values: "core_types.core.ValuesType") -> None:
        # No port validation needed, URLs are used
        pass

    @property
    def api(self) -> "client.OpenshiftClient":
        if self._cached_api is None:
            self._cached_api = client.OpenshiftClient(
                cluster_url=self.cluster_url.value,
                api_url=self.api_url.value,
                username=self.username.value,
                password=self.password.value,
                namespace=self.namespace.value or "default",
                cache=self.cache,
                timeout=self.timeout.as_int(),
                verify_ssl=self.verify_ssl.as_bool(),
            )
        return self._cached_api

    def test_connection(self) -> bool:
        return self.api.test()

    @cached("reachable", consts.cache.SHORT_CACHE_TIMEOUT)
    def is_available(self) -> bool:
        return self.api.test()

    @staticmethod
    @typing.override
    def test(env: "environment.Environment", data: "core_types.core.ValuesType") -> "core_types.core.TestResult":
        ov = OpenshiftProvider(env, data)
        if ov.test_connection() is True:
            return core_types.core.TestResult(True, _("Connection works fine"))

        return core_types.core.TestResult(False, _("Connection failed. Check connection params"))

    # Utility
    def sanitized_name(self, name: str) -> str:
        """
        Sanitizes the VM name to comply with RFC 1123:
        - Converts to lowercase
        - Replaces any character not in [a-z0-9.-] with '-'
        - Collapses multiple '-' into one
        - Removes leading/trailing non-alphanumeric characters
        - Limits length to 63 characters
        """
        name = name.lower()
        # Replace any character not allowed with '-'
        name = re.sub(r"[^a-z0-9.-]", "-", name)
        # Collapse multiple '-' into one
        name = re.sub(r"-{2,}", "-", name)
        # Remove leading/trailing non-alphanumeric characters
        name = re.sub(r"^[^a-z0-9]+|[^a-z0-9]+$", "", name)
        return name[:63]
