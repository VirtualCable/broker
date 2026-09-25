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

Base classes for the self-assessment checks.

One subclass of :class:`Check` is one check. Concrete checks live in
:mod:`uds.checks` (auto-discovered, like ``uds.services`` or ``uds.auths``)
and must define the ``id`` and ``category`` class variables and implement
:meth:`Check.run`.

The *kind* of a check (when it runs) is explicit and comes from the marker
class it derives from:

- :class:`AutomaticCheck`: fast, runs on every scan (``/system/checks``).
- :class:`ManualCheck`: slow and/or expensive, only runs on explicit request
  (``/system/manual_checks``).

New kinds (scheduled, startup, ...) are just new marker classes declaring
their :class:`uds.core.types.checks.CheckKind`; the runner never needs to
change.
"""

import abc
import typing

from uds.core import types
from uds.core.types.checks import CheckOutcome


class Check(abc.ABC):
    """Base class for every self-assessment check.

    Concrete checks must define:

    - ``id``: stable machine-readable identifier (e.g. ``"debug-enabled"``).
      Base and intermediate helper classes leave it empty, which keeps them
      out of the registry.
    - ``category``: the :class:`uds.core.types.checks.CheckCategory` the
      check belongs to (security or health).
    - ``description``: what the check verifies and how to fix it when it
      fails, marked with ``gettext_noop`` (translated when the report is built).

    and implement :meth:`run`.

    They must also derive from one of the kind marker classes
    (:class:`AutomaticCheck`, :class:`ManualCheck`, ...), which is what
    provides the ``kind`` class variable; deriving from :class:`Check`
    directly is a registration error.
    """

    #: Stable machine-readable identifier. Empty on base/helper classes.
    id: typing.ClassVar[str] = ""

    #: Area the check belongs to.
    category: typing.ClassVar[types.checks.CheckCategory]

    #: What the check verifies and how to fix it (untranslated, gettext_noop).
    description: typing.ClassVar[str]

    #: When this check runs. Provided by the kind marker subclasses;
    #: a check without a valid kind is rejected at registration time.
    kind: typing.ClassVar[types.checks.CheckKind]

    @abc.abstractmethod
    def run(self) -> CheckOutcome:
        """Evaluates the check condition.

        Returns:
            A :data:`CheckOutcome` tuple: severity, whether the check passes,
            a human readable detail and, optionally, the affected elements.
        """
        ...


class AutomaticCheck(Check):
    """Marker for *automatic* checks: fast ones, run on every scan.

    Anything deriving from this class (directly or through intermediate
    classes) runs with the regular scan (``/system/checks``).
    """

    kind: typing.ClassVar[types.checks.CheckKind] = types.checks.CheckKind.AUTOMATIC


class ManualCheck(Check):
    """Marker for *manual* checks: slow and/or expensive ones.

    Examples: comparing the real machines of a platform against the UDS
    inventory, deep scans of providers, ...

    They are excluded from the regular scan (``/system/checks``) and only
    run on explicit request (``/system/manual_checks``).
    """

    kind: typing.ClassVar[types.checks.CheckKind] = types.checks.CheckKind.MANUAL
