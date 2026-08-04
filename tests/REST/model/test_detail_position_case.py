# -*- coding: utf-8 -*-
"""
Concrete reproduction of the detail "position" mismatch, over the REST API.

The admin table is server side paginated: it lists with ``$top``/``$skip`` and, when a column
is sorted, ``$orderby``. After creating/editing an item it asks ``.../position/<uuid>`` and
jumps to ``floor(position / pageSize)``. The position endpoint receives none of those params,
so it counts rows in a different order than the one the table is showing.

Groups declare ``Meta.ordering = ("name",)``, so this has nothing to do with unordered
querysets: the mismatch happens with a model that already has a default order.
"""

import logging

from ...utils import rest

logger = logging.getLogger(__name__)

PAGE_SIZE = 10


class DetailPositionRealCaseTest(rest.test.RESTActorTestCase):
    def setUp(self) -> None:
        rest.test.NUMBER_OF_ITEMS_TO_CREATE = 16
        super().setUp()
        self.login()

    def _url(self) -> str:
        return f"authenticators/{self.auth.uuid}/groups"

    def _listing(self, query: str) -> list[str]:
        response = self.client.rest_get(f"{self._url()}/overview?{query}")
        self.assertEqual(response.status_code, 200)
        return [i["id"] for i in response.json()]

    def _position(self, uuid: str) -> int:
        response = self.client.rest_get(f"{self._url()}/position/{uuid}")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_position_ignores_the_orderby_the_table_is_using(self) -> None:
        descending = self._listing("$orderby=name desc")
        target = descending[0]  # first row the user sees when sorting by name descending

        position = self._position(target)
        page_the_client_jumps_to = position // PAGE_SIZE
        page_the_item_really_is_in = descending.index(target) // PAGE_SIZE

        page_shown = self._listing(f"$top={PAGE_SIZE}&$skip={page_the_client_jumps_to * PAGE_SIZE}&$orderby=name desc")

        logger.error(
            "orderby=name desc -> item is row %s (page %s), position endpoint says %s (page %s)",
            descending.index(target),
            page_the_item_really_is_in,
            position,
            page_the_client_jumps_to,
        )
        self.assertNotIn(target, page_shown, "expected the item NOT to be in the page the client jumps to")

    def test_position_ignores_the_filter_the_table_is_using(self) -> None:
        filtered = self._listing("$filter=contains(name, 'group1')")
        self.assertGreater(len(filtered), 0)
        target = filtered[-1]

        self.assertNotEqual(
            self._position(target),
            filtered.index(target),
            "position must match the index inside the filtered listing the table shows",
        )
