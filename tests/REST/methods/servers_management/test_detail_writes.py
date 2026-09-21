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
Safety net for the server-group detail writes (ServersServers.save_item).

Freezes the externally visible semantics a client relies on: the flat
saved item as the write answer (same shape GET returns), persistence of
both the server row and the group membership, and the error codes
(invalid MAC -> 400, unknown server -> 404).

Reference: src/uds/REST/methods/servers_management.py (class ServersServers)

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import logging
import typing
from unittest import mock

from uds import models
from uds.core import types

from ....fixtures import servers as servers_fixtures
from ....utils import rest

logger: logging.Logger = logging.getLogger(__name__)


class ServerGroupServersWritesTest(rest.test.RESTTestCase):
    """POST/PUT servers/groups/<uuid>/servers[/...] creates, adds and edits."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def test_create_unmanaged_answers_the_flat_item(self) -> None:
        group = servers_fixtures.create_server_group(type=types.servers.ServerType.UNMANAGED, num_servers=0)
        base = f"servers/groups/{group.uuid}/servers"
        response = self.client.rest_post(base, {"hostname": "unmanaged-one", "ip": "10.9.8.7", "mac": ""})
        self.assertEqual(response.status_code, 200, response.content)
        created = response.json()
        self.assertNotIn("result", created)
        server_uuid = created["id"]

        # Row and membership both persisted
        self.assertTrue(group.servers.filter(uuid=server_uuid).exists())

        get_resp = self.client.rest_get(f"{base}/{server_uuid}")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(created, get_resp.json())

    def test_create_unmanaged_rejects_invalid_mac(self) -> None:
        group = servers_fixtures.create_server_group(type=types.servers.ServerType.UNMANAGED, num_servers=0)
        response = self.client.rest_post(
            f"servers/groups/{group.uuid}/servers", {"hostname": "h", "ip": "1.2.3.4", "mac": "nope"}
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(group.servers.count(), 0)

    def test_add_existing_server_answers_the_flat_item(self) -> None:
        group = servers_fixtures.create_server_group(type=types.servers.ServerType.SERVER, num_servers=0)
        server = servers_fixtures.create_server(type=types.servers.ServerType.SERVER)
        base = f"servers/groups/{group.uuid}/servers"
        response = self.client.rest_post(base, {"server": server.uuid})
        self.assertEqual(response.status_code, 200, response.content)
        created = response.json()
        self.assertEqual(created["id"], server.uuid)

        self.assertTrue(group.servers.filter(uuid=server.uuid).exists())

        get_resp = self.client.rest_get(f"{base}/{server.uuid}")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(created, get_resp.json())

    def test_add_unknown_server_is_not_found(self) -> None:
        group = servers_fixtures.create_server_group(type=types.servers.ServerType.SERVER, num_servers=0)
        response = self.client.rest_post(
            f"servers/groups/{group.uuid}/servers", {"server": "00000000-0000-0000-0000-000000000000"}
        )
        self.assertEqual(response.status_code, 404, response.content)

    def test_edit_unmanaged_answers_the_flat_item(self) -> None:
        group = servers_fixtures.create_server_group(type=types.servers.ServerType.UNMANAGED, num_servers=0)
        base = f"servers/groups/{group.uuid}/servers"
        created = self.client.rest_post(base, {"hostname": "before-edit", "ip": "10.9.8.7", "mac": ""}).json()
        server_uuid = created["id"]

        response = self.client.rest_put(
            f"{base}/{server_uuid}", {"hostname": "after-edit", "ip": "10.9.8.7", "mac": ""}
        )
        self.assertEqual(response.status_code, 200, response.content)
        edited = response.json()
        self.assertEqual(edited["id"], server_uuid)

        server = group.servers.get(uuid=server_uuid)
        self.assertEqual(server.hostname, "after-edit")

        get_resp = self.client.rest_get(f"{base}/{server_uuid}")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(edited, get_resp.json())

    def test_edit_foreign_server_is_not_found(self) -> None:
        """A server outside the group's scope answers 404 without touching it."""
        group = servers_fixtures.create_server_group(type=types.servers.ServerType.UNMANAGED, num_servers=0)
        foreign = servers_fixtures.create_server(type=types.servers.ServerType.UNMANAGED)
        base = f"servers/groups/{group.uuid}/servers"
        response = self.client.rest_put(
            f"{base}/{foreign.uuid}", {"hostname": "stolen", "ip": "1.2.3.4", "mac": ""}
        )
        self.assertEqual(response.status_code, 404, response.content)
        foreign.refresh_from_db()
        self.assertNotEqual(foreign.hostname, "stolen")

    def test_failure_on_the_answer_read_rolls_the_write_back(self) -> None:
        """If serializing the answer fails, the created server is not persisted."""
        from uds.REST.methods.servers_management import ServersServers

        group = servers_fixtures.create_server_group(type=types.servers.ServerType.UNMANAGED, num_servers=0)
        with mock.patch.object(ServersServers, "get_item", side_effect=RuntimeError("serialization boom")):
            response = self.client.rest_post(
                f"servers/groups/{group.uuid}/servers", {"hostname": "doomed", "ip": "10.9.8.7", "mac": ""}
            )
        self.assertEqual(response.status_code, 500, response.content)
        self.assertEqual(group.servers.count(), 0)
        self.assertFalse(models.Server.objects.filter(hostname="doomed").exists())
