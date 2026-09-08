#
# Copyright (c) 2012-2023 Virtual Cable S.L.
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
import unittest

from uds.core.ui import gui
from uds.core.ui.user_interface import UserInterface


class _BaseWithSecret(UserInterface):
    """Declares one password field and one explicit extra sensitive field."""

    base_password = gui.PasswordField(
        label=typing.cast(str, "Base password"),
        order=1,
        tooltip=typing.cast(str, "Base password"),
        default="",
        length=64,
        required=False,
    )

    sensitive_fields = ("base_extra_secret",)


class _DerivedWithSecret(_BaseWithSecret):
    """Adds its own password field; must inherit the base one."""

    derived_password = gui.PasswordField(
        label=typing.cast(str, "Derived password"),
        order=2,
        tooltip=typing.cast(str, "Derived password"),
        default="",
        length=64,
        required=False,
    )

    sensitive_fields = ("derived_extra_secret",)


class SensitiveFieldsTest(unittest.TestCase):
    """``UserInterface.get_sensitive_fields()`` detection."""

    def test_detects_declared_password_fields(self) -> None:
        self.assertEqual(_BaseWithSecret.get_sensitive_fields(), {"base_password", "base_extra_secret"})

    def test_inherits_password_fields_and_overrides_extras(self) -> None:
        fields = _DerivedWithSecret.get_sensitive_fields()
        # Passwords of the whole hierarchy are detected...
        self.assertIn("base_password", fields)
        self.assertIn("derived_password", fields)
        # ...while ``sensitive_fields`` (a plain class attribute) follows the
        # normal python override rules, so only the most derived one applies.
        self.assertIn("derived_extra_secret", fields)
        self.assertNotIn("base_extra_secret", fields)

    def test_plain_class_has_no_sensitive_fields(self) -> None:
        self.assertEqual(UserInterface.get_sensitive_fields(), set())
