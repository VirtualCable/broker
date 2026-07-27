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
CRUD smoke test for the /authenticators handler (Phase 1 — Safety net).

Freezes the current upsert semantics of ModelHandler.put for authenticators.
These tests must keep passing after any future change. If any of them breaks,
it is a regression or an intentional change that must be updated explicitly.

Reference: src/uds/REST/methods/authenticators.py
           src/uds/REST/model/master/__init__.py (ModelHandler.put, :381)

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import logging
import typing

from uds import models

from ....utils import rest

logger = logging.getLogger(__name__)

# Test authenticator type available (src/uds/auths/InternalDB/authenticator.py)
TEST_AUTH_TYPE: typing.Final[str] = "InternalDBAuth"


class AuthenticatorsCrudTest(rest.test.RESTTestCase):
    """Freezes the CRUD lifecycle of /authenticators."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def _auth_count_in_db(self) -> int:
        return models.Authenticator.objects.all().count()

    def test_get_overview_returns_list(self) -> None:
        """GET /authenticators/overview returns 200 and a list with the DB authenticators."""
        response = self.client.rest_get("authenticators/overview")
        self.assertEqual(response.status_code, 200)
        items: list[dict[str, typing.Any]] = response.json()
        self.assertIsInstance(items, list)
        self.assertEqual(len(items), self._auth_count_in_db())

    def test_get_item_by_uuid(self) -> None:
        """GET /authenticators/<uuid> returns 200 and the matching item."""
        # self.auth comes from RESTTestCase.setUp
        response = self.client.rest_get(f"authenticators/{self.auth.uuid}")
        self.assertEqual(response.status_code, 200)
        item: dict[str, typing.Any] = response.json()
        self.assertEqual(item["id"], self.auth.uuid)
        self.assertEqual(item["name"], self.auth.name)

    def test_get_nonexistent_item_returns_404(self) -> None:
        """GET /authenticators/<nonexistent-uuid> returns 404."""
        response = self.client.rest_get("authenticators/00000000-0000-0000-0000-000000000000")
        self.assertEqual(response.status_code, 404)

    def test_put_creates_new_authenticator(self) -> None:
        """PUT /authenticators (no ID) creates a new authenticator."""
        before = self._auth_count_in_db()
        payload: dict[str, typing.Any] = {
            "name": "smoke-test-auth",
            "comments": "created by CRUD smoke test",
            "data_type": TEST_AUTH_TYPE,
            "tags": [],
            "priority": 1,
            "small_name": "smoke",
            "state": "A",
            "net_filtering": "D",  # DISABLED
        }
        response = self.client.rest_put("authenticators", data=payload)
        self.assertEqual(response.status_code, 200, response.content)
        item: dict[str, typing.Any] = response.json()
        self.assertIn("id", item)
        self.assertEqual(item["name"], "smoke-test-auth")

        after = self._auth_count_in_db()
        self.assertEqual(after, before + 1, "PUT create must add exactly one authenticator")
        self.assertTrue(models.Authenticator.objects.filter(uuid=item["id"]).exists())

    def test_put_updates_existing_authenticator(self) -> None:
        """PUT /authenticators/<uuid> updates an existing authenticator."""
        create_payload: dict[str, typing.Any] = {
            "name": "before-update-auth",
            "comments": "original",
            "data_type": TEST_AUTH_TYPE,
            "tags": [],
            "priority": 1,
            "small_name": "before",
            "state": "A",
            "net_filtering": "D",
        }
        create_resp = self.client.rest_put("authenticators", data=create_payload)
        self.assertEqual(create_resp.status_code, 200, create_resp.content)
        new_uuid: str = create_resp.json()["id"]

        update_payload: dict[str, typing.Any] = {
            "name": "after-update-auth",
            "comments": "cambiado",
            "data_type": TEST_AUTH_TYPE,
            "tags": [],
            "priority": 5,
            "small_name": "after",
            "state": "A",
            "net_filtering": "D",
        }
        update_resp = self.client.rest_put(f"authenticators/{new_uuid}", data=update_payload)
        self.assertEqual(update_resp.status_code, 200, update_resp.content)
        updated: dict[str, typing.Any] = update_resp.json()
        self.assertEqual(updated["id"], new_uuid)
        self.assertEqual(updated["name"], "after-update-auth")
        self.assertEqual(updated["comments"], "cambiado")
        self.assertEqual(updated["priority"], 5)

        db_auth = models.Authenticator.objects.get(uuid=new_uuid)
        self.assertEqual(db_auth.name, "after-update-auth")
        self.assertEqual(db_auth.priority, 5)

    def test_delete_authenticator(self) -> None:
        """DELETE /authenticators/<uuid> returns OK and removes the authenticator."""
        create_payload: dict[str, typing.Any] = {
            "name": "to-delete-auth",
            "comments": "",
            "data_type": TEST_AUTH_TYPE,
            "tags": [],
            "priority": 1,
            "small_name": "todelete",
            "state": "A",
            "net_filtering": "D",
        }
        create_resp = self.client.rest_put("authenticators", data=create_payload)
        new_uuid: str = create_resp.json()["id"]

        before = self._auth_count_in_db()
        delete_resp = self.client.rest_delete(f"authenticators/{new_uuid}")
        self.assertEqual(delete_resp.status_code, 200, delete_resp.content)
        self.assertEqual(delete_resp.json(), "ok")

        after = self._auth_count_in_db()
        self.assertEqual(after, before - 1, "DELETE must remove exactly one authenticator")
        self.assertFalse(models.Authenticator.objects.filter(uuid=new_uuid).exists())

    def test_get_after_delete_returns_404(self) -> None:
        """After DELETE, a GET of the same uuid returns 404."""
        create_payload: dict[str, typing.Any] = {
            "name": "to-delete-auth-2",
            "comments": "",
            "data_type": TEST_AUTH_TYPE,
            "tags": [],
            "priority": 1,
            "small_name": "todelete2",
            "state": "A",
            "net_filtering": "D",
        }
        create_resp = self.client.rest_put("authenticators", data=create_payload)
        new_uuid: str = create_resp.json()["id"]

        self.client.rest_delete(f"authenticators/{new_uuid}")
        response = self.client.rest_get(f"authenticators/{new_uuid}")
        self.assertEqual(response.status_code, 404)
