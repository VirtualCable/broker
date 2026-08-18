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

Factory used by ``uds.core.security.checks`` to register security self-assessment
checks by stable id.

A check is a zero-argument callable returning
``(SecurityCheckSeverity, bool, str)``. Group modules (settings, global_config,
models, logs, ...) plug into the factory via :meth:`Factory.register` so new
checks can be added without touching the runner.
"""

import collections.abc
import logging
import typing

from uds.core import types
from uds.core.util import factory

logger = logging.getLogger(__name__)

# A check evaluates a single security-relevant condition and returns a
# ``CheckResult``: its severity, whether it passes and a human readable detail.
CheckResult: typing.TypeAlias = tuple[types.security.SecurityCheckSeverity, bool, str]
CheckFn: typing.TypeAlias = collections.abc.Callable[[], CheckResult]


class SecurityChecksFactory(factory.Factory[CheckFn]):
    """Registry of security self-assessment checks keyed by stable id."""

    def register_check(self, check_id: str, check: CheckFn) -> None:
        """
        Inserts a check callable into the registry.

        Named ``register_check`` (not ``register``) so it does not collide with
        :meth:`Factory.register`, which expects a module class (``type[V]``)
        rather than an instance (``V``).
        """
        if check_id in self._objects:
            logger.debug("%s already registered as %s", check, self._objects[check_id])
            return

        # The base class types ``_objects`` as ``MutableMapping[str, type[V]]``,
        # but we are intentionally storing instances (``V``), not classes.
        self._objects[check_id.lower()] = check  # type: ignore[index]
