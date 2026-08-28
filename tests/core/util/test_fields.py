from uds.core import types
from uds.core.util import fields

from ...utils.test import UDSTestCase


class VerifySslFieldTabTest(UDSTestCase):
    # Support already knows where this checkbox lives in 4.0, so its placement is pinned
    # here: moving it would change every provider screen they have been trained on

    def test_field_has_no_tab_when_none_is_given(self) -> None:
        self.assertIsNone(fields.verify_ssl_field()._field_info.tab)

    def test_field_has_no_tab_when_false_is_given(self) -> None:
        self.assertIsNone(fields.verify_ssl_field(tab=False)._field_info.tab)

    def test_any_explicit_tab_lands_on_advanced(self) -> None:
        for tab in (types.ui.Tab.PARAMETERS, types.ui.Tab.TUNNEL, types.ui.Tab.ADVANCED):
            with self.subTest(tab=tab):
                self.assertEqual(fields.verify_ssl_field(tab=tab)._field_info.tab, types.ui.Tab.ADVANCED)

    def test_default_is_true_and_can_be_turned_off(self) -> None:
        self.assertTrue(fields.verify_ssl_field().default)
        self.assertFalse(fields.verify_ssl_field(default=False).default)
