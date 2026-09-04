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

Openuds-only coverage: ``IPMachinesService`` is the only registered model
that carries a ``server_group`` field in the openuds repo. Enterprise-side
coverage of ``RDSProvider`` lives in enterprise-server's
``test_server_group_usages.py``; per Adolfo, mixing open and enterprise
in the same test fixture is exactly what we must avoid — devs trust the
stub and the real path drifts.

The discovery helper is exercised end-to-end (scan + provider row + REST
endpoint) and the contract test verifies that what the discovery finds
matches what the openuds factory actually exposes.
"""

import logging

from uds import models
from uds.REST.methods.servers_management import _providers_using_server_group
from uds.core import environment
from uds.services.PhysicalMachines.service_multi import IPMachinesService
from uds.services.PhysicalMachines.service_single import IPSingleMachineService

from ....fixtures import servers as servers_fixtures
from ....utils import rest


def _make_ipmachines_provider() -> models.Provider:
    return models.Provider.objects.create(
        name="ipmachines-provider",
        comments="",
        data_type="PhysicalMachinesServiceProvider",
        data=IPSingleMachineService(environment.Environment.testing_environment(), None).serialize(),
    )


def _make_ipmachines_service(provider: models.Provider, name: str | None = None) -> models.Service:
    return provider.services.create(
        name=name or f"{IPMachinesService.type_type}-service",
        data_type=IPMachinesService.type_type,
        data=IPMachinesService(environment.Environment.testing_environment(), provider.get_instance()).serialize(),
        token=f"token-{IPMachinesService.type_type}-{provider.uuid}",
    )


def _attach_service_to_group(service: models.Service, group_uuid: str) -> None:
    instance = service.get_instance()
    instance.server_group.value = group_uuid
    service.data = instance.serialize()
    service.save(update_fields=["data"])


class ServerGroupUsagesTest(rest.test.RESTTestCase):
    def test_usages_empty_when_nothing_references_group(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=0)
        self.assertEqual(_providers_using_server_group(group.uuid), [])

    def test_usages_lists_ipmachines_service_referencing_group(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=0)
        provider = _make_ipmachines_provider()
        service = _make_ipmachines_service(provider)
        _attach_service_to_group(service, group.uuid)

        usages = _providers_using_server_group(group.uuid)
        self.assertEqual(len(usages), 1)
        self.assertEqual(usages[0]["uuid"], service.uuid)
        self.assertEqual(usages[0]["type"], IPMachinesService.type_type)
        self.assertEqual(usages[0]["kind"], "service")

    def test_usages_ignores_references_to_other_groups(self) -> None:
        target = servers_fixtures.create_server_group(num_servers=0)
        other = servers_fixtures.create_server_group(num_servers=0)
        provider = _make_ipmachines_provider()
        service = _make_ipmachines_service(provider)
        _attach_service_to_group(service, other.uuid)

        self.assertEqual(_providers_using_server_group(target.uuid), [])
        self.assertEqual(
            [u["uuid"] for u in _providers_using_server_group(other.uuid)],
            [service.uuid],
        )

    def test_usages_ignores_providers_not_in_constant(self) -> None:
        from tests.fixtures.modules.service.provider import TestProvider

        group = servers_fixtures.create_server_group(num_servers=0)
        # TestProvider is intentionally NOT in the discovery result
        # (no server_group field); the filter at the scan step skips it
        # without ever touching the instance.
        models.Provider.objects.create(
            name="unrelated-provider",
            comments="",
            data_type=TestProvider.type_type,
            data=models.Provider(name="tmp", comments="", data_type=TestProvider.type_type).get_instance().serialize(),
        )

        self.assertEqual(_providers_using_server_group(group.uuid), [])

    def test_usages_rest_endpoint_returns_usages(self) -> None:
        self.login()
        group = servers_fixtures.create_server_group(num_servers=0)
        provider = _make_ipmachines_provider()
        service = _make_ipmachines_service(provider)
        _attach_service_to_group(service, group.uuid)

        response = self.client.rest_get(f"servers/groups/{group.uuid}/usages")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["uuid"], service.uuid)
        self.assertEqual(body[0]["kind"], "service")

    def test_delete_logs_warning_when_group_in_use(self) -> None:
        self.login()
        group = servers_fixtures.create_server_group(num_servers=0)
        provider = _make_ipmachines_provider()
        service = _make_ipmachines_service(provider)
        _attach_service_to_group(service, group.uuid)

        with self.assertLogs("uds.REST.methods.servers_management", level="WARNING") as logs:
            response = self.client.rest_delete(f"servers/groups/{group.uuid}")
            self.assertEqual(response.status_code, 200, response.content)

        joined = "\n".join(logs.output)
        self.assertIn(group.uuid, joined)
        self.assertIn(group.name, joined)
        self.assertIn(service.name, joined)

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


class ServerGroupDiscoveryTest(rest.test.RESTTestCase):
    """Openuds-side discovery contract.

    Asserts that the discovery helper finds exactly the openuds-side
    classes that carry a server-group field. Enterprise-side coverage
    of ``RDSProvider`` lives in enterprise-server's
    ``test_server_group_usages.py``.
    """

    def test_discovery_finds_ipmachines_service(self) -> None:
        from uds.REST.methods.servers_management import _classes_with_server_group_field

        found = _classes_with_server_group_field()
        pairs = sorted((kind, cls.type_type) for kind, cls in found)

        self.assertIn(
            ("service", IPMachinesService.type_type),
            pairs,
            f"IPMachinesService should be discovered as a server-group service. Found: {pairs}.",
        )
        # IPSingleMachineService stores its host on a plain `host` field,
        # not on a ServerGroup — make sure the discovery doesn't pick it up.
        self.assertNotIn(
            ("service", IPSingleMachineService.type_type),
            pairs,
            f"IPSingleMachineService should not be discovered (uses a `host` field, not ServerGroup). Found: {pairs}.",
        )
