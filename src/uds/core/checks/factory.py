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

Registry of self-assessment checks, keyed by their stable id.

Check classes are inserted here by the auto-discovery of :mod:`uds.checks`
(the same mechanism used by ``uds.services``, ``uds.auths``, ...) so new
checks never need to touch this module or the runner.
"""

import logging

from uds.core import types
from uds.core.util import factory

from .base import Check

logger: logging.Logger = logging.getLogger(__name__)


class ChecksFactory(factory.Factory[Check]):
    """Registry of self-assessment checks keyed by stable id."""

    def insert(self, check: type[Check]) -> None:
        """
        Registers a check class under its own ``id``.

        Classes without an ``id`` (base or intermediate helper classes) or
        without a valid ``kind`` (deriving from ``Check`` directly, instead
        of a kind marker such as ``AutomaticCheck``/``ManualCheck``) are
        skipped: they are not runnable checks.
        """
        if not check.id:
            logger.debug("Check %s has no id, not registering it", check)
            return

        if getattr(check, "kind", None) not in types.checks.CheckKind:
            logger.error(
                "Check %s does not derive from a kind marker (AutomaticCheck/ManualCheck/...), not registering it",
                check,
            )
            return

        super().register(check.id, check)
