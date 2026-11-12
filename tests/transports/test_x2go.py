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
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import ast

from tests.utils.test import UDSTestCase
from uds.transports.X2GO.x2go import X2GOTransport

PUBLIC_KEY = "AAAAB3NzaC1yc2EAAAADAQABAAABAQ"


class X2GOAuthorizationScriptTest(UDSTestCase):
    def authorize_call_args(self, user: str) -> list[object]:
        transport = X2GOTransport(self.create_environment(), None)
        script = ast.parse(transport.get_authorization_script(user, PUBLIC_KEY))

        call = script.body[-1]
        assert isinstance(call, ast.Expr) and isinstance(call.value, ast.Call)
        self.assertEqual(ast.unparse(call.value.func), "update_authorized_keys")
        return [ast.literal_eval(arg) for arg in call.value.args]

    def test_plain_username(self) -> None:
        self.assertEqual(self.authorize_call_args("jdoe"), ["jdoe", PUBLIC_KEY])

    def test_username_with_quotes_stays_a_literal(self) -> None:
        user = "x', ''); __import__('os').system('id'); ('"

        self.assertEqual(self.authorize_call_args(user), [user, PUBLIC_KEY])

    def test_username_containing_key_placeholder_stays_a_literal(self) -> None:
        self.assertEqual(self.authorize_call_args("__KEY__"), ["__KEY__", PUBLIC_KEY])

    def test_username_with_non_ascii_and_backslash(self) -> None:
        user = "josé\\admin\n"

        self.assertEqual(self.authorize_call_args(user), [user, PUBLIC_KEY])
