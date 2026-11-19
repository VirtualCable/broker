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
Author: Andres Schumann, aschumann at virtualcable dot es
"""

import typing
import unittest

from uds.core.ui import gui


class TestChoiceFieldValue(unittest.TestCase):
    def test_explicit_value_is_returned(self) -> None:
        field = gui.ChoiceField(label="Choice", order=1, choices=["a", "b"], value="b")

        self.assertEqual(field.value, "b")

    def test_default_is_used_when_there_is_no_value(self) -> None:
        field = gui.ChoiceField(label="Choice", order=1, choices=["a", "b"], default="b")

        self.assertEqual(field.value, "b")

    def test_first_choice_is_used_when_there_is_no_value_nor_default(self) -> None:
        field = gui.ChoiceField(label="Choice", order=1, choices=["first", "second"])

        self.assertEqual(field.value, "first")

    def test_first_choice_is_resolved_from_a_callable_choices_source(self) -> None:
        def choices() -> list[typing.Any]:
            return gui.as_choices(["from-callback", "other"])

        field = gui.ChoiceField(label="Choice", order=1, choices=choices)

        self.assertEqual(field.value, "from-callback")
