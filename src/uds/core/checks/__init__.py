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

This package holds the *general* machinery (like ``uds.core.providers`` does
for providers): the :class:`Check` base class, the :class:`ChecksFactory`
registry and the runner (:func:`run_checks`, :func:`build_report`).

The concrete checks live in :mod:`uds.checks` and are auto-discovered at app
startup, the same way ``uds.services`` or ``uds.auths`` are: every
:class:`Check` subclass found under that package is registered in the factory
under its ``id``. One class, one check; each class declares its
:class:`uds.core.types.checks.CheckCategory` as a class variable and implements
:meth:`Check.run`.

Checks only *notify*: they never modify any configuration value.
"""

from .base import Check as Check
from .base import CheckOutcome as CheckOutcome
from .factory import ChecksFactory as ChecksFactory
from .runner import build_report as build_report
from .runner import run_checks as run_checks


def factory() -> ChecksFactory:
    """Returns the singleton :class:`ChecksFactory`."""
    return ChecksFactory()
