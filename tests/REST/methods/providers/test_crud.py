# -*- coding: utf-8 -*-
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
# AND EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
# IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
# OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
CRUD smoke test for the /providers handler (Phase 1 — Safety net).

Freezes the current upsert semantics of ModelHandler.put for providers:
- PUT without ID in path  -> CREATE
- PUT with ID in path     -> UPDATE
- DELETE by ID            -> OK
- GET overview / <id>     -> list / item

Reference: src/uds/REST/methods/providers.py
           src/uds/REST/model/master/__init__.py (ModelHandler.put, :381)

These tests must keep passing after any future change (QUERY,
PUT-create -> POST migration, etc.). If any of them breaks, it is a
regression or an intentional change that must be updated explicitly.

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import logging
import typing

from uds import models

from ....utils import rest

logger = logging.getLogger(__name__)

# Test provider type available in the test environment.
# Must exist in services.factory().providers()
TEST_PROVIDER_TYPE: typing.Final[str] = "TestProvider"


class ProvidersCrudTest(rest.test.RESTTestCase):
    """Freezes the CRUD lifecycle of /providers."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def _provider_count_in_db(self) -> int:
        return models.Provider.objects.all().count()

    def test_get_overview_returns_list(self) -> None:
        """GET /providers/overview returns 200 and a list with the DB providers."""
        response = self.client.rest_get("providers/overview")
        self.assertEqual(response.status_code, 200)
        items: list[dict[str, typing.Any]] = response.json()
        self.assertIsInstance(items, list)
        self.assertEqual(len(items), self._provider_count_in_db())

    def test_get_item_by_uuid(self) -> None:
        """GET /providers/<uuid> returns 200 and the matching item."""
        # self.provider comes from RESTTestCase.setUp
        response = self.client.rest_get(f"providers/{self.provider.uuid}")
        self.assertEqual(response.status_code, 200)
        item: dict[str, typing.Any] = response.json()
        self.assertEqual(item["id"], self.provider.uuid)
        self.assertEqual(item["name"], self.provider.name)

    def test_get_nonexistent_item_returns_404(self) -> None:
        """GET /providers/<nonexistent-uuid> returns 404."""
        response = self.client.rest_get("providers/00000000-0000-0000-0000-000000000000")
        self.assertEqual(response.status_code, 404)

    def test_put_creates_new_provider(self) -> None:
        """PUT /providers (no ID) creates a new provider and returns the item with uuid."""
        before = self._provider_count_in_db()
        payload: dict[str, typing.Any] = {
            "name": "smoke-test-provider",
            "comments": "created by CRUD smoke test",
            "data_type": TEST_PROVIDER_TYPE,
            "tags": [],
        }
        response = self.client.rest_put("providers", data=payload)
        self.assertEqual(response.status_code, 200, response.content)
        item: dict[str, typing.Any] = response.json()
        self.assertIn("id", item)
        self.assertEqual(item["name"], "smoke-test-provider")

        after = self._provider_count_in_db()
        self.assertEqual(after, before + 1, "PUT create must add exactly one provider")
        # And it must exist in the DB
        self.assertTrue(models.Provider.objects.filter(uuid=item["id"]).exists())

    def test_put_updates_existing_provider(self) -> None:
        """PUT /providers/<uuid> updates an existing provider."""
        # Create one via the API
        create_payload: dict[str, typing.Any] = {
            "name": "before-update",
            "comments": "original",
            "data_type": TEST_PROVIDER_TYPE,
            "tags": [],
        }
        create_resp = self.client.rest_put("providers", data=create_payload)
        self.assertEqual(create_resp.status_code, 200, create_resp.content)
        created: dict[str, typing.Any] = create_resp.json()
        new_uuid = created["id"]

        # Update it
        update_payload: dict[str, typing.Any] = {
            "name": "after-update",
            "comments": "cambiado",
            "data_type": TEST_PROVIDER_TYPE,
            "tags": [],
        }
        update_resp = self.client.rest_put(f"providers/{new_uuid}", data=update_payload)
        self.assertEqual(update_resp.status_code, 200, update_resp.content)
        updated: dict[str, typing.Any] = update_resp.json()
        self.assertEqual(updated["id"], new_uuid)
        self.assertEqual(updated["name"], "after-update")
        self.assertEqual(updated["comments"], "cambiado")

        # And it persists in the DB
        db_provider = models.Provider.objects.get(uuid=new_uuid)
        self.assertEqual(db_provider.name, "after-update")

    def test_delete_provider(self) -> None:
        """DELETE /providers/<uuid> returns OK and removes the provider."""
        # Create one without services (so validate_delete does not fail)
        create_payload: dict[str, typing.Any] = {
            "name": "to-delete",
            "comments": "",
            "data_type": TEST_PROVIDER_TYPE,
            "tags": [],
        }
        create_resp = self.client.rest_put("providers", data=create_payload)
        self.assertEqual(create_resp.status_code, 200, create_resp.content)
        new_uuid: str = create_resp.json()["id"]

        before = self._provider_count_in_db()
        delete_resp = self.client.rest_delete(f"providers/{new_uuid}")
        self.assertEqual(delete_resp.status_code, 200, delete_resp.content)
        # Success body is 'ok' (consts.OK)
        self.assertEqual(delete_resp.json(), "ok")

        after = self._provider_count_in_db()
        self.assertEqual(after, before - 1, "DELETE must remove exactly one provider")
        self.assertFalse(models.Provider.objects.filter(uuid=new_uuid).exists())

    def test_get_after_delete_returns_404(self) -> None:
        """After DELETE, a GET of the same uuid returns 404 (DELETE idempotency)."""
        create_payload: dict[str, typing.Any] = {
            "name": "to-delete-2",
            "comments": "",
            "data_type": TEST_PROVIDER_TYPE,
            "tags": [],
        }
        create_resp = self.client.rest_put("providers", data=create_payload)
        new_uuid: str = create_resp.json()["id"]

        self.client.rest_delete(f"providers/{new_uuid}")
        # Second GET (or GET after delete) -> 404
        response = self.client.rest_get(f"providers/{new_uuid}")
        self.assertEqual(response.status_code, 404)
