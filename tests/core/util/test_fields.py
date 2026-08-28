from uds.core import types
from uds.core.util import fields

from ...utils.test import UDSTestCase


class VerifySslFieldTabTest(UDSTestCase):
    def test_tab_defaults_to_advanced(self) -> None:
        self.assertEqual(fields.verify_ssl_field()._field_info.tab, types.ui.Tab.ADVANCED)

    def test_tab_false_falls_back_to_advanced(self) -> None:
        self.assertEqual(fields.verify_ssl_field(tab=False)._field_info.tab, types.ui.Tab.ADVANCED)

    def test_explicit_tab_is_respected(self) -> None:
        self.assertEqual(
            fields.verify_ssl_field(tab=types.ui.Tab.PARAMETERS)._field_info.tab, types.ui.Tab.PARAMETERS
        )
        self.assertEqual(fields.verify_ssl_field(tab=types.ui.Tab.TUNNEL)._field_info.tab, types.ui.Tab.TUNNEL)

    def test_default_value_is_kept_true(self) -> None:
        self.assertTrue(fields.verify_ssl_field().default)
        self.assertFalse(fields.verify_ssl_field(default=False).default)
