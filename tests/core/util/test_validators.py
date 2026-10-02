#
# Copyright (c) 2022 Virtual Cable S.L.
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

import logging

from uds.core import exceptions
from uds.core.util import validators

from ...utils.test import UDSTestCase

logger: logging.Logger = logging.getLogger(__name__)


class ValidatePasswordComplexityTest(UDSTestCase):
    """
    Contracts for validators.validate_password_complexity.
    """

    def test_accepts_valid_passwords(self) -> None:
        for password in (
            "Aa1!aaaa",  # 8 chars, all four categories
            "abcdefgh1A",  # 8 chars, three categories
            "ThisIsAVeryLongP4ssword",  # long, three categories
            "aaaaaaaaaa1A",  # exactly minimum categories
        ):
            with self.subTest(password=password):
                self.assertEqual(validators.validate_password_complexity(password), password)

    def test_rejects_short_password(self) -> None:
        with self.assertRaises(exceptions.ui.ValidationError):
            validators.validate_password_complexity("Aa1!")

    def test_rejects_too_long_password(self) -> None:
        with self.assertRaises(exceptions.ui.ValidationError):
            validators.validate_password_complexity("Aa1!" + "x" * 200)

    def test_rejects_single_category(self) -> None:
        with self.assertRaises(exceptions.ui.ValidationError):
            validators.validate_password_complexity("aaaaaaaa")

    def test_rejects_two_categories_below_minimum(self) -> None:
        # 2 categories, shipped minimum is 3
        with self.assertRaises(exceptions.ui.ValidationError):
            validators.validate_password_complexity("abcdefgh1234")

    def test_accepts_two_categories_when_reconfigured(self) -> None:
        password = "abcdefgh1234"
        self.assertEqual(
            validators.validate_password_complexity(password, min_categories=2),
            password,
        )

    def test_honors_min_length_override(self) -> None:
        password = "Aa1!"
        self.assertEqual(validators.validate_password_complexity(password, min_length=4), password)
        with self.assertRaises(exceptions.ui.ValidationError):
            validators.validate_password_complexity(password, min_length=5)

    def test_field_name_in_message(self) -> None:
        with self.assertRaises(exceptions.ui.ValidationError) as cm:
            validators.validate_password_complexity("short", field_name="Custom field")
        self.assertIn("Custom field", str(cm.exception))
