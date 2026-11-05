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
Author: Andres Schumann, aschumann at virtualcable dot es
"""

import collections.abc
import contextlib
import functools
import typing

from uds.core import exceptions
from uds.core.services.generics.dynamic.service import DynamicService

from ....utils.test import UDSTestCase
from ....services.openstack import fixtures as openstack_fixtures
from ....services.proxmox import fixtures as proxmox_fixtures
from ....services.xen import fixtures as xen_fixtures

BOTH_FLAGS: typing.Final[dict[str, bool]] = {"remove_duplicates": True, "maintain_on_error": True}

ServiceFactory = collections.abc.Callable[..., DynamicService]


@contextlib.contextmanager
def proxmox_factory() -> collections.abc.Generator[ServiceFactory, None, None]:
    with proxmox_fixtures.patched_provider() as provider:
        yield functools.partial(proxmox_fixtures.create_service_linked, provider=provider)


@contextlib.contextmanager
def openstack_factory() -> collections.abc.Generator[ServiceFactory, None, None]:
    with openstack_fixtures.patched_provider() as provider:
        yield functools.partial(openstack_fixtures.create_live_service, provider)


@contextlib.contextmanager
def xen_factory() -> collections.abc.Generator[ServiceFactory, None, None]:
    with xen_fixtures.patched_provider() as provider:
        yield functools.partial(xen_fixtures.create_service_linked, provider=provider)


FACTORIES: typing.Final = (proxmox_factory, openstack_factory, xen_factory)


class RemoveDuplicatesKeepOnErrorTest(UDSTestCase):
    def test_saving_with_both_flags_is_rejected(self) -> None:
        for factory in FACTORIES:
            with (
                self.subTest(service=factory.__name__),
                factory() as create,
                self.assertRaises(exceptions.ui.ValidationError),
            ):
                create(**BOTH_FLAGS)

    def test_saving_with_one_flag_is_allowed(self) -> None:
        for factory in FACTORIES:
            for flags in (
                {"remove_duplicates": True, "maintain_on_error": False},
                {"remove_duplicates": False, "maintain_on_error": True},
            ):
                with self.subTest(service=factory.__name__, flags=flags), factory() as create:
                    service = create(**flags)
                    self.assertEqual(service.remove_duplicates.value, flags["remove_duplicates"])
                    self.assertEqual(service.maintain_on_error.value, flags["maintain_on_error"])

    def test_loading_stored_service_with_both_flags_still_works(self) -> None:
        for factory in FACTORIES:
            with self.subTest(service=factory.__name__), factory() as create:
                service = create(remove_duplicates=True, maintain_on_error=False)
                service.maintain_on_error.value = True
                stored = service.marshal()

                loaded = create(remove_duplicates=False, maintain_on_error=False)
                loaded.unmarshal(stored)

                self.assertTrue(loaded.remove_duplicates.value)
                self.assertTrue(loaded.maintain_on_error.value)
