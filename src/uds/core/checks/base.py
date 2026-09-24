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

Base class for the self-assessment checks.

One subclass of :class:`Check` is one check. Concrete checks live in
:mod:`uds.checks` (auto-discovered, like ``uds.services`` or ``uds.auths``)
and must define the ``id`` and ``category`` class variables and implement
:meth:`Check.run`.
"""

import typing

from uds.core import types

# A check evaluates a single condition and returns a ``CheckOutcome``: its
# severity, whether it passes and a human readable detail. The severity is
# part of the outcome (not a class attribute) because several checks graduate
# it depending on the observed magnitude.
CheckOutcome: typing.TypeAlias = tuple[types.checks.CheckSeverity, bool, str]


class Check:
    """Base class for every self-assessment check.

    Concrete checks must define:

    - ``id``: stable machine-readable identifier (e.g. ``"debug-enabled"``).
      Base and intermediate helper classes leave it empty, which keeps them
      out of the registry.
    - ``category``: the :class:`uds.core.types.checks.CheckCategory` the
      check belongs to (security or health).

    and implement :meth:`run`.
    """

    #: Stable machine-readable identifier. Empty on base/helper classes.
    id: typing.ClassVar[str] = ""

    #: Area the check belongs to.
    category: typing.ClassVar[types.checks.CheckCategory]

    def run(self) -> CheckOutcome:
        """Evaluates the check condition.

        Returns:
            A :data:`CheckOutcome` tuple: severity, whether the check passes
            and a human readable detail.
        """
        raise NotImplementedError
