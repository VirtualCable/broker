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

Self-assessment checks for the OpenUDS broker.

Each check evaluates a single configuration or state condition and returns its
severity, whether it passes and a human readable detail. Checks declare their
:class:`uds.core.types.checks.CheckCategory` (``SECURITY`` or ``HEALTH``) at
registration time.

Checks are grouped by data source:

- :mod:`uds.core.checks.settings`      -- ``django.conf.settings``
- :mod:`uds.core.checks.global_config` -- admin-editable global config
- :mod:`uds.core.checks.models`        -- live database state
- :mod:`uds.core.checks.logs`          -- runtime log/state
- :mod:`uds.core.checks.webhook_queue` -- webhook delivery queue

Every group exposes a :func:`register_checks` that plugs its callables into the
shared :class:`ChecksFactory` returned by :func:`factory`. The runner
(:func:`run_checks`, :func:`build_report`) walks whatever the factory currently
holds; new checks only need to register, no edits to the runner.

Checks only *notify*: they never modify any configuration value.
"""

from . import global_config as global_config
from . import logs as logs
from . import models as models
from . import settings as settings
from . import webhook_queue as webhook_queue
from .factory import CheckEntry as CheckEntry
from .factory import CheckFn as CheckFn
from .factory import CheckOutcome as CheckOutcome
from .factory import ChecksFactory as ChecksFactory
from .runner import build_report as build_report
from .runner import run_checks as run_checks


def factory() -> ChecksFactory:
    """Returns the singleton :class:`ChecksFactory`."""
    return ChecksFactory()


def _initialize() -> None:
    """Registers every check group into the singleton factory.

    Called once at import time; re-invoking it is harmless because the
    factory itself ignores duplicate check ids.
    """
    fact = ChecksFactory()
    for group in (settings, global_config, models, logs, webhook_queue):
        group.register_checks(fact)


_initialize()
