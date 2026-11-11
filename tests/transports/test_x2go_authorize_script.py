"""
The X2Go authorization script receives the username as a Python literal, so a
username cannot close the string and add statements to the script.
"""

import ast
import typing

from tests.utils.test import UDSTestCase
from uds.transports.X2GO.x2go import X2GOTransport

PUBLIC_KEY: typing.Final = "AAAAB3NzaC1yc2EAAAADAQABAAABAQC"


class X2GOAuthorizeScriptTest(UDSTestCase):
    def _update_call_args(self, user: str) -> list[ast.expr]:
        transport = X2GOTransport(self.create_environment(), None)
        tree = ast.parse(transport.get_authorization_script(user, PUBLIC_KEY))
        calls = [
            node.value
            for node in tree.body
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "update_authorized_keys"
        ]
        self.assertEqual(len(calls), 1)
        return calls[0].args

    def test_username_is_passed_as_a_single_literal(self) -> None:
        user = "alice"
        args = self._update_call_args(user)
        self.assertEqual(len(args), 2)
        self.assertIsInstance(args[0], ast.Constant)
        self.assertEqual(args[0].value, user)

    def test_quote_in_username_does_not_add_statements(self) -> None:
        user = "a');print('X');('"
        transport = X2GOTransport(self.create_environment(), None)
        tree = ast.parse(transport.get_authorization_script(user, PUBLIC_KEY))
        top_level_calls = [node for node in tree.body if isinstance(node, ast.Expr)]
        injected_prints = [
            node
            for node in top_level_calls
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "print"
        ]
        self.assertEqual(injected_prints, [])
        args = self._update_call_args(user)
        self.assertEqual(args[0].value, user)  # type: ignore[attr-defined]

    def test_public_key_placeholder_is_replaced_before_username(self) -> None:
        args = self._update_call_args("__KEY__")
        self.assertEqual(args[0].value, "__KEY__")  # type: ignore[attr-defined]
        self.assertEqual(args[1].value, PUBLIC_KEY)  # type: ignore[attr-defined]
