from unittest import mock

from uds.core import types
from uds.core.util import ui as ui_utils

from ...utils.test import UDSTestCase

# Compiled catalogs (.mo) are build artifacts, so translations are simulated here
TRANSLATED_ADVANCED: str = 'Avanzado'


def _fake_gettext(text: str) -> str:
    return TRANSLATED_ADVANCED if text == types.ui.Tab.ADVANCED else text


class GuiBuilderTabTest(UDSTestCase):
    def test_tabs_are_translated(self) -> None:
        with mock.patch.object(ui_utils, 'gettext', _fake_gettext):
            fields = (
                ui_utils.GuiBuilder()
                .add_stock_field(types.rest.stock.StockField.NETWORKS)
                .new_tab(types.ui.Tab.ADVANCED)
                .add_text(name='label', label='Label')
                .build()
            )

        self.assertEqual({field.gui.tab for field in fields}, {TRANSLATED_ADVANCED})

    def test_translation_does_not_modify_stock_fields(self) -> None:
        with mock.patch.object(ui_utils, 'gettext', _fake_gettext):
            ui_utils.GuiBuilder().add_stock_field(types.rest.stock.StockField.NETWORKS).build()

        stock_fields = types.rest.stock.StockField.NETWORKS.get_fields()
        self.assertEqual({field.gui.tab for field in stock_fields}, {types.ui.Tab.ADVANCED})
