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
Safety net for the meta pool detail writes (MetaServicesPool and
MetaAssignedService save_item paths, plus the pool groups assignment
shared with service pools).

Freezes what a client must see through the HTTP layer: the flat saved
item as the write answer (same shape GET returns), persistence, and the
error semantics (400 for request mistakes, 404 for unknown objects).

Reference: src/uds/REST/methods/meta_service_pools.py

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import logging
import typing
from unittest import mock

from uds import models

from ....fixtures import services as services_fixtures
from ....utils import rest

logger: logging.Logger = logging.getLogger(__name__)


class MetaPoolMembersWritesTest(rest.test.RESTTestCase):
    """POST/PUT metapools/<uuid>/pools manages member pools."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        self.meta = services_fixtures.create_db_metapool(service_pools=[], groups=self.groups)
        service = services_fixtures.create_db_service(self.provider)
        osmanager = services_fixtures.create_db_osmanager()
        self.pool = services_fixtures.create_db_servicepool(service, osmanager, self.groups)

    def test_add_member_answers_the_flat_item(self) -> None:
        base = f"metapools/{self.meta.uuid}/pools"
        response = self.client.rest_post(base, {"pool_id": self.pool.uuid, "priority": "3", "enabled": "T"})
        self.assertEqual(response.status_code, 200, response.content)
        created = response.json()
        self.assertNotIn("result", created)
        member_uuid = created["id"]

        member = self.meta.members.get(uuid=member_uuid)
        self.assertEqual(member.pool.uuid, self.pool.uuid)
        self.assertEqual(member.priority, 3)
        self.assertTrue(member.enabled)

        get_resp = self.client.rest_get(f"{base}/{member_uuid}")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(created, get_resp.json())

    def test_edit_member_answers_the_flat_item(self) -> None:
        base = f"metapools/{self.meta.uuid}/pools"
        created = self.client.rest_post(
            base, {"pool_id": self.pool.uuid, "priority": "1", "enabled": "T"}
        ).json()
        member_uuid = created["id"]

        response = self.client.rest_put(
            f"{base}/{member_uuid}", {"pool_id": self.pool.uuid, "priority": "5", "enabled": "false"}
        )
        self.assertEqual(response.status_code, 200, response.content)
        edited = response.json()
        self.assertEqual(edited["id"], member_uuid)
        self.assertEqual(edited["priority"], 5)
        self.assertFalse(edited["enabled"])

        get_resp = self.client.rest_get(f"{base}/{member_uuid}")
        self.assertEqual(edited, get_resp.json())

    def test_add_member_unknown_pool_is_not_found(self) -> None:
        response = self.client.rest_post(
            f"metapools/{self.meta.uuid}/pools",
            {"pool_id": "00000000-0000-0000-0000-000000000000", "priority": "1", "enabled": "T"},
        )
        self.assertEqual(response.status_code, 404, response.content)
        self.assertEqual(self.meta.members.count(), 0)

    def test_failure_on_the_answer_read_rolls_the_write_back(self) -> None:
        """If serializing the answer fails, the member row is not persisted."""
        from uds.REST.methods.meta_service_pools import MetaServicesPool

        with mock.patch.object(MetaServicesPool, "get_item", side_effect=RuntimeError("serialization boom")):
            response = self.client.rest_post(
                f"metapools/{self.meta.uuid}/pools",
                {"pool_id": self.pool.uuid, "priority": "3", "enabled": "T"},
            )
        self.assertEqual(response.status_code, 500, response.content)
        self.assertEqual(models.MetaPoolMember.objects.filter(pool=self.pool).count(), 0)


class MetaPoolAssignedWritesTest(rest.test.RESTTestCase):
    """PUT metapools/<uuid>/services/<id> changes the owner of an assigned service."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        service = services_fixtures.create_db_service(self.provider)
        osmanager = services_fixtures.create_db_osmanager()
        member_pool = services_fixtures.create_db_servicepool(service, osmanager, self.groups)
        self.meta = services_fixtures.create_db_metapool(service_pools=[member_pool], groups=self.groups)
        publication = services_fixtures.create_db_publication(member_pool)
        self.owner_a, self.owner_b = self.plain_users[0], self.plain_users[1]
        self.userservice = services_fixtures.create_db_userservice(member_pool, publication, self.owner_a)

    def test_owner_change_answers_the_flat_item(self) -> None:
        base = f"metapools/{self.meta.uuid}/services"
        response = self.client.rest_put(
            f"{base}/{self.userservice.uuid}", {"auth_id": self.auth.uuid, "user_id": self.owner_b.uuid}
        )
        self.assertEqual(response.status_code, 200, response.content)
        edited = response.json()
        self.assertNotIn("result", edited)
        self.assertEqual(edited["id"], self.userservice.uuid)
        self.assertEqual(edited["owner_info"]["user_id"], self.owner_b.uuid)

        self.userservice.refresh_from_db()
        assert self.userservice.user is not None
        self.assertEqual(self.userservice.user.uuid, self.owner_b.uuid)

        get_resp = self.client.rest_get(f"{base}/{self.userservice.uuid}")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(edited, get_resp.json())

    def test_unknown_userservice_is_not_found(self) -> None:
        response = self.client.rest_put(
            f"metapools/{self.meta.uuid}/services/00000000-0000-0000-0000-000000000000",
            {"auth_id": self.auth.uuid, "user_id": self.owner_b.uuid},
        )
        self.assertEqual(response.status_code, 404, response.content)

    def test_failure_on_the_answer_read_rolls_the_write_back(self) -> None:
        """If serializing the answer fails, the ownership change is not persisted."""
        from uds.REST.methods.meta_service_pools import MetaAssignedService

        with mock.patch.object(MetaAssignedService, "get_item", side_effect=RuntimeError("serialization boom")):
            response = self.client.rest_put(
                f"metapools/{self.meta.uuid}/services/{self.userservice.uuid}",
                {"auth_id": self.auth.uuid, "user_id": self.owner_b.uuid},
            )
        self.assertEqual(response.status_code, 500, response.content)
        self.userservice.refresh_from_db()
        assert self.userservice.user is not None
        self.assertEqual(self.userservice.user.uuid, self.owner_a.uuid)


class MetaPoolGroupsWritesTest(rest.test.RESTTestCase):
    """POST metapools/<uuid>/groups assigns a group; answers the flat item."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        self.meta = services_fixtures.create_db_metapool(service_pools=[], groups=[])
        self.group = self.simple_groups[0]

    def test_assign_answers_the_flat_item(self) -> None:
        base = f"metapools/{self.meta.uuid}/groups"
        response = self.client.rest_post(base, {"id": self.group.uuid})
        self.assertEqual(response.status_code, 200, response.content)
        created = response.json()
        self.assertEqual(created["id"], self.group.uuid)
        self.assertEqual(created["name"], self.group.name)

        self.assertIn(self.group.uuid, [g.uuid for g in self.meta.assignedGroups.all()])

        get_resp = self.client.rest_get(f"{base}/{self.group.uuid}")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(created, get_resp.json())
