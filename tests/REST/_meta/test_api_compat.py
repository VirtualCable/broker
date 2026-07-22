# -*- coding: utf-8 -*-
#
# Copyright (c) 2023 Virtual Cable S.L.
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
"""

import typing

from uds.core.types.rest import ApiCompat

from tests.utils import rest


class ApiCompatContractTest(rest.test.RESTTestCase):
    """Freeze the ApiCompat enum and Handler.api_compat() contract."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    # ------------------------------------------------------------------
    # Enum contract
    # ------------------------------------------------------------------
    def test_has_exactly_two_members(self) -> None:
        """ApiCompat exposes exactly COMPAT and NO_COMPAT."""
        assert set(ApiCompat) == {ApiCompat.COMPAT, ApiCompat.NO_COMPAT}

    def test_compat_value(self) -> None:
        """COMPAT member has the expected string value."""
        self.assertEqual(ApiCompat.COMPAT.value, "COMPAT")
        self.assertIsInstance(ApiCompat.COMPAT, str)

    def test_no_compat_value(self) -> None:
        """NO_COMPAT member has the expected string value."""
        self.assertEqual(ApiCompat.NO_COMPAT.value, "NO_COMPAT")
        self.assertIsInstance(ApiCompat.NO_COMPAT, str)

    def test_is_string_enum(self) -> None:
        """Members compare equal to their plain string values."""
        self.assertEqual(ApiCompat.COMPAT, "COMPAT")
        self.assertEqual(ApiCompat.NO_COMPAT, "NO_COMPAT")

    def test_members_are_distinct(self) -> None:
        """COMPAT and NO_COMPAT are different values."""
        self.assertNotEqual(ApiCompat.COMPAT, ApiCompat.NO_COMPAT)

    # ------------------------------------------------------------------
    # Handler.api_compat() contract
    # ------------------------------------------------------------------
    def test_api_compat_inherited_by_providers(self) -> None:
        """Concrete Handler subclasses inherit api_compat()."""
        from uds.REST.handlers import Handler
        from uds.REST.methods.providers import Providers

        self.assertTrue(hasattr(Providers, "api_compat"))
        self.assertTrue(issubclass(Providers, Handler))  # pyright: ignore[reportUnnecessaryIsInstance]

    def test_api_compat_default_is_compat(self) -> None:
        """The hardcoded default in Handler.api_compat is COMPAT (v5)."""
        import inspect

        from uds.REST.handlers import Handler

        source = inspect.getsource(Handler.api_compat)
        self.assertIn("COMPAT", source)
