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

Concrete self-assessment checks for the OpenUDS broker.

One class, one check: every :class:`uds.core.checks.Check` subclass found
under this package is auto-registered at import time (same mechanism as
``uds.services``, ``uds.auths``, ...) in the checks factory, keyed by its
``id``.

To add a new check, simply create a subclass in any module of this package
defining ``id``, ``category`` and :meth:`uds.core.checks.Check.run`. Nothing
else needs to be touched.
"""

import logging

from uds.core.checks import Check, ChecksFactory
from uds.core.util import modfinder

logger: logging.Logger = logging.getLogger(__name__)


def __load_modules() -> None:
    """Imports every module of this package and registers the check classes found.

    Classes with an empty ``id`` (base or intermediate helper classes) are
    skipped by the factory itself.
    """
    modfinder.dynamically_load_and_register_packages(
        ChecksFactory().insert,
        Check,
        __name__,
        checker=lambda cls: bool(cls.id),
    )


__load_modules()
