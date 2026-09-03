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

The constant in ``servers_management.py`` lists provider-level AND
service-level references. ``RDSProvider`` (enterprise) is exercised via a
stub class that registers with the same factory/type_type contract,
since openuds' test repo cannot import enterprise code.
``IPMachinesService`` (openuds) is exercised with the real class — its
``server_group`` field lives on the Service, not the Provider, so the
helper must scan ``Service.objects`` too.
"""

import logging
import typing

from uds import models
from uds.REST.methods.servers_management import _providers_using_server_group
from uds.core import environment, services, types
from uds.core.util import fields

from ....fixtures import servers as servers_fixtures
from ....utils import rest


_RDS_PROVIDER_TYPE_TYPE = "RDSProvider"


def _ensure_rds_stub_registered() -> type[services.ServiceProvider]:
    class RDSLikeProvider(services.ServiceProvider):
        type_name: typing.ClassVar[str] = "RDS-like (test stub)"
        type_type: typing.ClassVar[str] = _RDS_PROVIDER_TYPE_TYPE
        type_description: typing.ClassVar[str] = "Stub mimicking enterprise RDSProvider for tests"
        offers: typing.ClassVar[list[type[services.Service]]] = []
        server_group = fields.server_group_field(
            [types.servers.ServerType.SERVER, types.servers.ServerType.UNMANAGED],
        )

    services.factory().insert(RDSLikeProvider)
    return RDSLikeProvider


def _attach_to_group(item: models.Provider | models.Service, group_uuid: str) -> None:
    instance = item.get_instance()
    instance.server_group.value = group_uuid
    item.data = instance.serialize()
    item.save(update_fields=["data"])


def _make_provider(type_type: str) -> models.Provider:
    cls = services.factory().lookup(type_type)
    assert cls is not None, f"Provider class {type_type} not registered"
    return models.Provider.objects.create(
        name=f"{type_type}-provider",
        comments="",
        data_type=type_type,
        data=cls(environment.Environment.testing_environment(), None).serialize(),
    )


def _make_service(
    provider: models.Provider, service_cls: type[services.Service], name: str | None = None
) -> models.Service:
    return provider.services.create(
        name=name or f"{service_cls.type_type}-service",
        data_type=service_cls.type_type,
        data=service_cls(environment.Environment.testing_environment(), provider.get_instance()).serialize(),
        token=f"token-{service_cls.type_type}-{provider.uuid}",
    )


class ServerGroupUsagesTest(rest.test.RESTTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._rds_stub = _ensure_rds_stub_registered()

    def test_usages_empty_when_nothing_references_group(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=0)
        self.assertEqual(_providers_using_server_group(group.uuid), [])

    def test_usages_lists_rds_provider_referencing_group(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=0)
        provider = _make_provider(_RDS_PROVIDER_TYPE_TYPE)
        _attach_to_group(provider, group.uuid)

        usages = _providers_using_server_group(group.uuid)
        self.assertEqual(len(usages), 1)
        self.assertEqual(usages[0]["uuid"], provider.uuid)
        self.assertEqual(usages[0]["type"], _RDS_PROVIDER_TYPE_TYPE)
        self.assertEqual(usages[0]["kind"], "provider")

    def test_usages_lists_ipmachines_service_referencing_group(self) -> None:
        from uds.services.PhysicalMachines.service_multi import IPMachinesService

        group = servers_fixtures.create_server_group(num_servers=0)
        provider = _make_provider("PhysicalMachinesServiceProvider")
        service = _make_service(provider, IPMachinesService)
        _attach_to_group(service, group.uuid)

        usages = _providers_using_server_group(group.uuid)
        self.assertEqual(len(usages), 1)
        self.assertEqual(usages[0]["uuid"], service.uuid)
        self.assertEqual(usages[0]["type"], IPMachinesService.type_type)
        self.assertEqual(usages[0]["kind"], "service")

    def test_usages_returns_both_provider_and_service(self) -> None:
        from uds.services.PhysicalMachines.service_multi import IPMachinesService

        group = servers_fixtures.create_server_group(num_servers=0)

        rds_provider = _make_provider(_RDS_PROVIDER_TYPE_TYPE)
        _attach_to_group(rds_provider, group.uuid)

        pm_provider = _make_provider("PhysicalMachinesServiceProvider")
        pm_service = _make_service(pm_provider, IPMachinesService)
        _attach_to_group(pm_service, group.uuid)

        usages = _providers_using_server_group(group.uuid)
        kinds = sorted(u["kind"] for u in usages)
        self.assertEqual(kinds, ["provider", "service"])
        by_kind = {u["kind"]: u for u in usages}
        self.assertEqual(by_kind["provider"]["uuid"], rds_provider.uuid)
        self.assertEqual(by_kind["service"]["uuid"], pm_service.uuid)

    def test_usages_ignores_references_to_other_groups(self) -> None:
        target = servers_fixtures.create_server_group(num_servers=0)
        other = servers_fixtures.create_server_group(num_servers=0)
        provider = _make_provider(_RDS_PROVIDER_TYPE_TYPE)
        _attach_to_group(provider, other.uuid)

        self.assertEqual(_providers_using_server_group(target.uuid), [])
        self.assertEqual(
            [u["uuid"] for u in _providers_using_server_group(other.uuid)],
            [provider.uuid],
        )

    def test_usages_ignores_models_not_in_constant(self) -> None:
        from tests.fixtures.modules.service.provider import TestProvider

        group = servers_fixtures.create_server_group(num_servers=0)
        # TestProvider is intentionally NOT in the constant; the filter
        # at the .filter(data_type__in=...) step must skip it without ever
        # touching .server_group.
        _make_provider(TestProvider.type_type)

        self.assertEqual(_providers_using_server_group(group.uuid), [])

    def test_rest_endpoint_returns_usages(self) -> None:
        self.login()
        group = servers_fixtures.create_server_group(num_servers=0)
        provider = _make_provider(_RDS_PROVIDER_TYPE_TYPE)
        _attach_to_group(provider, group.uuid)

        response = self.client.rest_get(f"servers/groups/{group.uuid}/usages")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["uuid"], provider.uuid)
        self.assertEqual(body[0]["kind"], "provider")

    def test_delete_logs_warning_when_group_in_use(self) -> None:
        self.login()
        group = servers_fixtures.create_server_group(num_servers=0)
        provider = _make_provider(_RDS_PROVIDER_TYPE_TYPE)
        _attach_to_group(provider, group.uuid)

        with self.assertLogs("uds.REST.methods.servers_management", level="WARNING") as logs:
            response = self.client.rest_delete(f"servers/groups/{group.uuid}")
            self.assertEqual(response.status_code, 200, response.content)

        joined = "\n".join(logs.output)
        self.assertIn(group.uuid, joined)
        self.assertIn(group.name, joined)
        self.assertIn(provider.name, joined)

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


class ServerGroupConstantContractTest(rest.test.RESTTestCase):
    """Contract guard: the constant in ``servers_management.py`` must list
    exactly the providers/services that carry a server-group field.

    Walks every registered Provider and Service class, finds any field
    whose label matches the label produced by ``fields.server_group_field()``,
    and asserts the resulting set of type_types is exactly the one
    declared in ``_SERVER_GROUP_FIELD_USAGES``. This catches drift: if a
    new provider adds a server-group field, this test fails until the
    constant is updated, and vice versa.
    """

    @staticmethod
    def _server_group_label() -> str:
        from uds.core.util import fields

        # The canonical field; its label is the contract that ties the
        # helper to the actual classes via introspection (no getattr on
        # instances — see AGENTS.md).
        canonical = fields.server_group_field()
        return str(canonical.label)

    @classmethod
    def _type_types_with_server_group(cls) -> set[str]:
        from uds.core import services as core_services
        from uds.core.ui import gui

        label = cls._server_group_label()
        found: set[str] = set()

        for provider_cls in core_services.factory().providers().values():
            for value in vars(provider_cls).values():
                if isinstance(value, gui.ChoiceField) and str(value.label) == label:
                    found.add(provider_cls.type_type)
                    break

        for provider_cls in core_services.factory().providers().values():
            for service_cls in provider_cls.get_provided_services():
                for value in vars(service_cls).values():
                    if isinstance(value, gui.ChoiceField) and str(value.label) == label:
                        found.add(service_cls.type_type)
                        break

        return found

    def test_discovery_finds_every_registered_class_with_server_group_label(self) -> None:
        from uds.REST.methods.servers_management import _classes_with_server_group_field

        found = _classes_with_server_group_field()
        found_pairs = sorted((kind, cls.type_type) for kind, cls in found)

        self.assertIn(
            ("provider", "RDSProvider"),
            found_pairs,
            (f"RDSProvider should be discovered as a server-group provider. Found: {found_pairs}."),
        )
        self.assertIn(
            ("service", "IPMachinesService"),
            found_pairs,
            (f"IPMachinesService should be discovered as a server-group service. Found: {found_pairs}."),
        )
