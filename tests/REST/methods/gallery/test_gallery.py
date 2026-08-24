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
size, and the full base64 of the image on the detail. The overview is
frozen as it behaves today (same payload as the listing), see the note on
test_overview_returns_the_same_payload_as_the_listing.

Reference: src/uds/REST/methods/images.py
           src/uds/REST/model/master/__init__.py (get_items, :311)

Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import logging
import typing

from uds import models

from ....fixtures import images as images_fixtures
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

    def test_overview_returns_the_same_payload_as_the_listing(self) -> None:
        # ModelHandler.get_items defaults to sumarize=False and the OVERVIEW branch
        # calls it with no arguments, so get_item_summary is never reached and the
        # overview ships the full image. Freezing what it does today; if this breaks,
        # the summary path came back to life and it is an intentional change.
        overview = self.client.rest_get("gallery/images/overview")
        listing = self.client.rest_get("gallery/images")
        self.assertEqual(overview.status_code, 200, overview.content)
        self.assertEqual(overview.json(), listing.json())

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
