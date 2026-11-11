"""
Author: Andres Schumann, aschumann at virtualcable dot es
"""

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

from .harvester import client
from .service import HarvesterService

# Not imported at runtime, just for type checking
if typing.TYPE_CHECKING:
    from uds.core import environment
    from uds.core.services import Service

logger: logging.Logger = logging.getLogger(__name__)


def cache_key_helper(self: 'HarvesterProvider') -> str:
    return self.api.cache_key()


class HarvesterProvider(ServiceProvider):
    offers: typing.ClassVar[list[type['Service']]] = [HarvesterService]
    type_name = _('SUSE Virtualization')
    type_type = 'HarvesterProvider'
    type_description = _('SUSE Virtualization (Harvester/KubeVirt) based VMs provider')
    icon_file = 'provider.png'
    group = core_types.services.ProviderGrouping.CLOUD_PROVIDERS

    api_url = gui.TextField(
        order=1,
        length=256,
        label=_('Harvester API URL'),
        tooltip=_('Harvester management URL, e.g. https://harvester.example.com'),
        required=True,
        default='',
    )
    api_token = gui.PasswordField(
        order=2,
        length=256,
        label=_('API token'),
        tooltip=_(
            'Harvester API key (GUI: User > Generate API Key). Expires as configured in Harvester.'
        ),
        required=True,
        default='',
    )
    namespace = gui.TextField(
        order=3,
        length=64,
        label=_('Namespace'),
        tooltip=_('Harvester/Kubernetes namespace to create the VMs on (default: "default")'),
        required=True,
        default='default',
    )
    verify_ssl = fields.verify_ssl_field(order=4)
    concurrent_creation_limit = fields.concurrent_creation_limit_field()
    concurrent_removal_limit = fields.concurrent_removal_limit_field()
    timeout = fields.timeout_field()

    _cached_api: 'client.HarvesterClient | None' = None

    @typing.override
    def initialize(self, values: 'core_types.core.ValuesType') -> None:
        self._cached_api = None  # Config may have changed, do not reuse the client

    @property
    def api(self) -> 'client.HarvesterClient':
        if self._cached_api is None:
            self._cached_api = client.HarvesterClient(
                endpoint=self.api_url.value.rstrip('/'),
                api_token=self.api_token.value,
                namespace=self.namespace.value or 'default',
                timeout=self.timeout.as_int(),
                verify_ssl=self.verify_ssl.as_bool(),
            )
        return self._cached_api

    def test_connection(self) -> bool:
        return self.api.test()

    @cached(
        'reachable',
        consts.cache.SHORT_CACHE_TIMEOUT,
        key_helper=cache_key_helper,
    )
    def is_available(self) -> bool:
        return self.api.test()

    @staticmethod
    @typing.override
    def test(
        env: 'environment.Environment', data: 'core_types.core.ValuesType'
    ) -> 'core_types.core.TestResult':
        ov = HarvesterProvider(env, data)
        if ov.test_connection() is True:
            return core_types.core.TestResult(True, _('Connection works fine'))
        return core_types.core.TestResult(False, _('Connection failed. Check connection params'))

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
        name = re.sub(r'[^a-z0-9.-]', '-', name)
        # Collapse multiple '-' into one
        name = re.sub(r'-{2,}', '-', name)
        # Remove leading/trailing non-alphanumeric characters
        name = re.sub(r'^[^a-z0-9]+|[^a-z0-9]+$', '', name)
        return name[:63]
