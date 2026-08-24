#
# Copyright (c) 2025 Virtual Cable S.L.
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
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
# IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
# OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
CRUD and overview tests for the /gallery/images handler.

Freezes the payload the admin gallery table feeds on: name, thumbnail and
size, and the full base64 of the image on the detail. The overview must not
carry the base64: that is the whole point of Images.get_item_summary.

The same file covers /gallery/servicespoolgroups, the other handler with a
summary of its own: its table paints a thumbnail that only the summary fills.

Reference: src/uds/REST/methods/images.py
           src/uds/REST/methods/services_pool_groups.py
           src/uds/REST/model/master/__init__.py (get_items, :311)

Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import logging
import typing
from unittest import mock

from uds import models
from uds.REST.methods import images

from ....fixtures import images as images_fixtures
from ....fixtures import services as services_fixtures
from ....utils import rest

logger = logging.getLogger(__name__)


class GalleryImagesTest(rest.test.RESTTestCase):
    """Freezes the payload of /gallery/images and its overview."""

    image: models.Image

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.image = images_fixtures.createImage()
        self.login()

    def test_overview_keeps_the_thumbnail_and_drops_the_image(self) -> None:
        overview = self.client.rest_get("gallery/images/overview")
        self.assertEqual(overview.status_code, 200, overview.content)

        item = next(i for i in overview.json() if i["id"] == self.image.uuid)
        self.assertEqual(item["thumb"], self.image.thumb64)
        self.assertFalse(item.get("data"))

    def test_overview_asks_for_a_summary(self) -> None:
        # Guards the dispatch itself: get_items defaults to sumarize=False, so the
        # OVERVIEW branch has to pass it explicitly or every summary goes unused.
        with mock.patch.object(
            images.Images, "get_items", autospec=True, return_value=iter([])
        ) as get_items:
            self.client.rest_get("gallery/images/overview")
        get_items.assert_called_once()
        self.assertTrue(get_items.call_args.kwargs["sumarize"])

    def test_overview_is_not_the_listing(self) -> None:
        overview = self.client.rest_get("gallery/images/overview")
        listing = self.client.rest_get("gallery/images")
        self.assertNotEqual(overview.json(), listing.json())

    def test_overview_carries_the_columns_the_table_paints(self) -> None:
        response = self.client.rest_get("gallery/images/overview")
        self.assertEqual(response.status_code, 200, response.content)
        items: list[dict[str, typing.Any]] = response.json()
        self.assertEqual(len(items), models.Image.objects.count())

        item = next(i for i in items if i["id"] == self.image.uuid)
        self.assertEqual(item["name"], self.image.name)
        self.assertEqual(item["thumb"], self.image.thumb64)
        self.assertIn(f"{self.image.width}x{self.image.height}", item["size"])

    def test_list_carries_the_full_image(self) -> None:
        response = self.client.rest_get("gallery/images")
        self.assertEqual(response.status_code, 200, response.content)
        items: list[dict[str, typing.Any]] = response.json()

        item = next(i for i in items if i["id"] == self.image.uuid)
        self.assertEqual(item["data"], self.image.data64)
        self.assertEqual(item["thumb"], self.image.thumb64)

    def test_get_one_image_carries_the_full_image(self) -> None:
        response = self.client.rest_get(f"gallery/images/{self.image.uuid}")
        self.assertEqual(response.status_code, 200, response.content)
        item: dict[str, typing.Any] = response.json()
        self.assertEqual(item["id"], self.image.uuid)
        self.assertEqual(item["data"], self.image.data64)

    def test_table_declares_the_columns_the_payload_fills(self) -> None:
        response = self.client.rest_get("gallery/images/tableinfo")
        self.assertEqual(response.status_code, 200, response.content)
        fields: set[str] = {name for column in response.json()["fields"] for name in column}
        self.assertIn("thumb", fields)
        self.assertIn("size", fields)
        self.assertIn("name", fields)

    def test_get_nonexistent_image_returns_404(self) -> None:
        response = self.client.rest_get("gallery/images/00000000-0000-0000-0000-000000000000")
        self.assertEqual(response.status_code, 404)

    def test_put_creates_an_image_with_thumbnail_and_size(self) -> None:
        before = models.Image.objects.count()
        response = self.client.rest_put(
            "gallery/images",
            data={"name": "smoke-test-image", "data": self.image.data64},
        )
        self.assertEqual(response.status_code, 200, response.content)
        item: dict[str, typing.Any] = response.json()

        self.assertEqual(models.Image.objects.count(), before + 1)
        created = models.Image.objects.get(uuid=item["id"])
        self.assertEqual(created.name, "smoke-test-image")
        # image setter is what fills these, so an empty thumb means the save path broke
        self.assertTrue(created.thumb)
        self.assertGreater(created.width, 0)
        self.assertGreater(created.height, 0)

    def test_delete_removes_the_image(self) -> None:
        response = self.client.rest_delete(f"gallery/images/{self.image.uuid}")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(models.Image.objects.filter(uuid=self.image.uuid).exists())


class GalleryServicePoolGroupsTest(rest.test.RESTTestCase):
    """Freezes the payload of /gallery/servicespoolgroups and its overview."""

    group: models.ServicePoolGroup

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.group = services_fixtures.create_db_servicepool_group(image=images_fixtures.createImage())
        self.login()

    def test_overview_carries_the_thumbnail_the_table_paints(self) -> None:
        response = self.client.rest_get("gallery/servicespoolgroups/overview")
        self.assertEqual(response.status_code, 200, response.content)

        item = next(i for i in response.json() if i["id"] == self.group.uuid)
        self.assertEqual(item["name"], self.group.name)
        self.assertEqual(item["thumb"], self.group.thumb64)

    def test_detail_carries_the_image_id_instead_of_the_thumbnail(self) -> None:
        response = self.client.rest_get(f"gallery/servicespoolgroups/{self.group.uuid}")
        self.assertEqual(response.status_code, 200, response.content)
        item: dict[str, typing.Any] = response.json()
        self.assertEqual(item["image_id"], self.group.image.uuid if self.group.image else None)
