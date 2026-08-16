#
# Copyright (c) 2024 Virtual Cable S.L.
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
Helpers for checking that modules are registered in their factories.

Generic: contains no knowledge about OSS vs enterprise modules, so it can be
safely used from both the OSS and the enterprise test suites.
"""

import collections.abc
import typing

from uds.core import module
from uds.core.util import factory

T = typing.TypeVar("T", bound=module.Module)


def check_registered(
    factory_instance: factory.ModuleFactory[T],
    names: collections.abc.Iterable[str],
    *,
    what: str = "module",
) -> None:
    """
    Assert that every name in ``names`` is registered in ``factory_instance``.

    Fails listing the missing ones, so new modules must be registered here
    when created.

    Args:
        factory_instance: The factory to check against.
        names: Iterable of module names that must be registered.
        what: Human-readable name of the module type, used in the error message
              (e.g. "provider", "authenticator").
    """
    missing = [name for name in names if not factory_instance.has(name)]
    assert not missing, f"{what} not registered: {', '.join(missing)}"
