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
#    * Neither the name of Virtual Cable S.L.U. nor the names of its contributors
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
Tests for the ServerGroup 'usages' custom method and its delete warning.
"""

import logging

from uds import models
from uds.REST.methods.servers_management import _providers_using_server_group

from ....fixtures import servers as servers_fixtures
from ....fixtures import services as services_fixtures
from ....utils import rest


class ServerGroupUsagesTest(rest.test.RESTTestCase):
    def _attach_provider_to_group(self, group: models.ServerGroup) -> models.Provider:
        provider = services_fixtures.create_db_provider()
        instance = provider.get_instance()
        instance.server_group.value = group.uuid
        provider.data = instance.serialize()
        provider.save(update_fields=["data"])
        return provider

    def test_usages_empty_when_no_provider_references_group(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=0)
        self.assertEqual(_providers_using_server_group(group.uuid), [])

    def test_usages_lists_provider_referencing_group(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=0)
        provider = self._attach_provider_to_group(group)

        usages = _providers_using_server_group(group.uuid)
        self.assertEqual(len(usages), 1)
        self.assertEqual(usages[0]["uuid"], provider.uuid)
        self.assertEqual(usages[0]["type"], provider.data_type)

    def test_usages_ignores_providers_referencing_other_groups(self) -> None:
        target = servers_fixtures.create_server_group(num_servers=0)
        other = servers_fixtures.create_server_group(num_servers=0)
        provider = self._attach_provider_to_group(other)

        self.assertEqual(_providers_using_server_group(target.uuid), [])
        self.assertEqual([u["uuid"] for u in _providers_using_server_group(other.uuid)], [provider.uuid])

    def test_usages_rest_endpoint_returns_providers(self) -> None:
        self.login()
        group = servers_fixtures.create_server_group(num_servers=0)
        provider = self._attach_provider_to_group(group)

        response = self.client.rest_get(f"servers/groups/{group.uuid}/usages")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["uuid"], provider.uuid)

    def test_delete_logs_warning_when_group_in_use(self) -> None:
        self.login()
        group = servers_fixtures.create_server_group(num_servers=0)
        self._attach_provider_to_group(group)

        with self.assertLogs("uds.REST.methods.servers_management", level="WARNING") as logs:
            response = self.client.rest_delete(f"servers/groups/{group.uuid}")
            self.assertEqual(response.status_code, 200, response.content)

        joined = "\n".join(logs.output)
        self.assertIn(group.uuid, joined)
        self.assertIn(group.name, joined)

    def test_delete_silently_when_group_unused(self) -> None:
        self.login()
        group = servers_fixtures.create_server_group(num_servers=0)

        logger_name = "uds.REST.methods.servers_management"
        before = logging.getLogger(logger_name).getEffectiveLevel()
        logging.getLogger(logger_name).setLevel(logging.ERROR)
        try:
            response = self.client.rest_delete(f"servers/groups/{group.uuid}")
            self.assertEqual(response.status_code, 200, response.content)
        finally:
            logging.getLogger(logger_name).setLevel(before)
