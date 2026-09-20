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
Safety net for the service-pool detail writes: groups, transports and the
assigned/cached services endpoints.

Freezes, through the HTTP layer, what a client must see: the flat saved
item (same shape GET returns), its persistence in the database, and the
error semantics (400 for request mistakes, 404 for unknown or
out-of-scope objects — assigned and cached services cannot be edited
through each other's URL).

Reference: src/uds/REST/methods/user_services.py

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import logging
import typing

from uds.core import types

from ....fixtures import services as services_fixtures
from ....utils import rest

logger: logging.Logger = logging.getLogger(__name__)


class PoolGroupsWritesTest(rest.test.RESTTestCase):
    """POST servicespools/<uuid>/groups assigns a group; answers the flat item."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        service = services_fixtures.create_db_service(self.provider)
        osmanager = services_fixtures.create_db_osmanager()
        self.pool = services_fixtures.create_db_servicepool(service, osmanager, [])
        self.group = self.simple_groups[0]

    def test_assign_answers_the_flat_item(self) -> None:
        base = f"servicespools/{self.pool.uuid}/groups"
        response = self.client.rest_post(base, {"id": self.group.uuid})
        self.assertEqual(response.status_code, 200, response.content)
        created = response.json()
        self.assertNotIn("result", created)
        self.assertEqual(created["id"], self.group.uuid)
        self.assertEqual(created["name"], self.group.name)

        # The write reached the database
        self.assertIn(self.group.uuid, [g.uuid for g in self.pool.assignedGroups.all()])

        get_resp = self.client.rest_get(f"{base}/{self.group.uuid}")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(created, get_resp.json())

    def test_assign_unknown_group_is_not_found(self) -> None:
        response = self.client.rest_post(
            f"servicespools/{self.pool.uuid}/groups", {"id": "00000000-0000-0000-0000-000000000000"}
        )
        self.assertEqual(response.status_code, 404, response.content)


class PoolTransportsWritesTest(rest.test.RESTTestCase):
    """POST servicespools/<uuid>/transports assigns a transport; answers the flat item."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        service = services_fixtures.create_db_service(self.provider)
        osmanager = services_fixtures.create_db_osmanager()
        self.pool = services_fixtures.create_db_servicepool(service, osmanager, [])
        self.transport = services_fixtures.create_db_transport()

    def test_assign_answers_the_flat_item(self) -> None:
        base = f"servicespools/{self.pool.uuid}/transports"
        response = self.client.rest_post(base, {"id": self.transport.uuid})
        self.assertEqual(response.status_code, 200, response.content)
        created = response.json()
        self.assertEqual(created["id"], self.transport.uuid)
        self.assertEqual(created["name"], self.transport.name)

        self.assertIn(self.transport.uuid, [t.uuid for t in self.pool.transports.all()])

        get_resp = self.client.rest_get(f"{base}/{self.transport.uuid}")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(created, get_resp.json())

    def test_assign_unknown_transport_is_not_found(self) -> None:
        response = self.client.rest_post(
            f"servicespools/{self.pool.uuid}/transports", {"id": "00000000-0000-0000-0000-000000000000"}
        )
        self.assertEqual(response.status_code, 404, response.content)


class AssignedServiceWritesTest(rest.test.RESTTestCase):
    """PUT servicespools/<uuid>/services/<id> (owner) and /cache/<id> (cached)."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        service = services_fixtures.create_db_service(self.provider, use_caching_version=True)
        osmanager = services_fixtures.create_db_osmanager()
        self.pool = services_fixtures.create_db_servicepool(service, osmanager, self.groups)
        publication = services_fixtures.create_db_publication(self.pool)
        self.owner_a, self.owner_b, self.owner_c = self.plain_users[0], self.plain_users[1], self.plain_users[2]
        self.assigned = services_fixtures.create_db_userservice(self.pool, publication, self.owner_a)
        self.other = services_fixtures.create_db_userservice(self.pool, publication, self.owner_b)
        # A cached service: same table, cache level set, no owner
        self.cached = services_fixtures.create_db_userservice(self.pool, publication, None)
        self.cached.cache_level = types.services.CacheLevel.L1
        self.cached.save(update_fields=["cache_level"])

    def test_owner_change_answers_the_flat_item(self) -> None:
        base = f"servicespools/{self.pool.uuid}/services"
        response = self.client.rest_put(
            f"{base}/{self.assigned.uuid}", {"auth_id": self.auth.uuid, "user_id": self.owner_c.uuid}
        )
        self.assertEqual(response.status_code, 200, response.content)
        edited = response.json()
        self.assertNotIn("result", edited)
        self.assertEqual(edited["id"], self.assigned.uuid)
        self.assertEqual(edited["owner_info"]["user_id"], self.owner_c.uuid)

        self.assigned.refresh_from_db()
        assert self.assigned.user is not None
        self.assertEqual(self.assigned.user.uuid, self.owner_c.uuid)

        get_resp = self.client.rest_get(f"{base}/{self.assigned.uuid}")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(edited, get_resp.json())

    def test_duplicate_owner_is_refused(self) -> None:
        response = self.client.rest_put(
            f"servicespools/{self.pool.uuid}/services/{self.assigned.uuid}",
            {"auth_id": self.auth.uuid, "user_id": self.owner_b.uuid},
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn(b"already", response.content)

    def test_invalid_fields_are_refused(self) -> None:
        response = self.client.rest_put(f"servicespools/{self.pool.uuid}/services/{self.assigned.uuid}", {})
        self.assertEqual(response.status_code, 400, response.content)

    def test_ip_change_answers_the_flat_item(self) -> None:
        response = self.client.rest_put(
            f"servicespools/{self.pool.uuid}/services/{self.assigned.uuid}", {"ip": "10.20.30.40"}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["id"], self.assigned.uuid)

    def test_cached_service_cannot_be_edited_through_assigned_url(self) -> None:
        """Risk-4 cut: the assigned endpoint answers 404 *before* any write."""
        response = self.client.rest_put(
            f"servicespools/{self.pool.uuid}/services/{self.cached.uuid}",
            {"auth_id": self.auth.uuid, "user_id": self.owner_c.uuid},
        )
        self.assertEqual(response.status_code, 404, response.content)
        self.cached.refresh_from_db()
        self.assertIsNone(self.cached.user)

    def test_assigned_service_cannot_be_edited_through_cache_url(self) -> None:
        """The reverse direction is cut the same way."""
        response = self.client.rest_put(
            f"servicespools/{self.pool.uuid}/cache/{self.assigned.uuid}",
            {"auth_id": self.auth.uuid, "user_id": self.owner_c.uuid},
        )
        self.assertEqual(response.status_code, 404, response.content)
        self.assigned.refresh_from_db()
        assert self.assigned.user is not None
        self.assertEqual(self.assigned.user.uuid, self.owner_a.uuid)
