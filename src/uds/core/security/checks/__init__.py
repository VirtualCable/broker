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
Author: Adolfo Gómez, dkmaster at dkmon dot com

Security self-assessment checks for the OpenUDS broker.

Each check evaluates a single security-relevant configuration condition and
returns its severity, whether it passes and a human readable detail.

Checks are grouped by data source:

- :mod:`uds.core.security.checks.settings`      -- ``django.conf.settings``
- :mod:`uds.core.security.checks.global_config` -- admin-editable global config
- :mod:`uds.core.security.checks.models`        -- live database state
- :mod:`uds.core.security.checks.logs`          -- runtime log/state (planned)

Every group exposes a :func:`register_checks` that plugs its callables into the
shared :class:`SecurityChecksFactory` returned by :func:`factory`. The runner
(:func:`runner.run_security_checks`) walks whatever the factory currently holds;
new checks only need to register, no edits to the runner.

Checks only *notify*: they never modify any configuration value.
"""

import typing

from uds.core import consts

from . import global_config as global_config
from . import logs as logs
from . import models as models
from . import settings as settings
from .factory import CheckFn as CheckFn
from .factory import SecurityChecksFactory as SecurityChecksFactory


def factory() -> SecurityChecksFactory:
    """Returns the singleton :class:`SecurityChecksFactory`."""
    return SecurityChecksFactory()


# Re-export so callers can import the constant from the package root without
# reaching into ``uds.core.consts.security`` directly.
DEFAULT_SUPERUSER_PASSWORD: typing.Final[str] = consts.security.DEFAULT_SUPERUSER_PASSWORD
