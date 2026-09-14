"""
The admin forms keep working against the readonly enforcement of ModelHandler.put.

The admin resends every gui field, readonly ones included, and stringifies
numbers, so a plain edit must still be accepted. Two form shapes are not
covered by the enforcement and are marked as expected failures: the service
pool (no ``data_type``, so no gui can be resolved) and any provider (its gui
elements are named ``instance.<field>``, which never matches a parameter).

Reference: src/uds/REST/model/master/__init__.py (ModelHandler._check_readonly_params)

Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import typing
import unittest

from uds import models

from ...fixtures import authenticators as fixtures_auths
from ...fixtures import services as fixtures_services
from ...utils import rest


class ReadonlyAdminFormTest(rest.test.RESTTestCase):
    """Round trips with the payload shape the admin really sends."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def _roundtrip(self, path: str) -> typing.Any:
        item = self.client.rest_get(path).json()
        return self.client.rest_put(path, data=item)

    def _proxmox_provider(self) -> models.Provider:
        provider = models.Provider(name="Proxmox provider", comments="", data_type="ProxmoxPlatform")
        provider.save()
        provider.data = provider.get_instance().serialize()
        provider.save()
        return provider

    def _stored_start_vmid(self, provider: models.Provider) -> int:
        return models.Provider.objects.get(uuid=provider.uuid).get_instance().start_vmid.value

    def test_authenticator_roundtrip_is_accepted(self) -> None:
        auth = fixtures_auths.create_db_authenticator()
        response = self._roundtrip(f"authenticators/{auth.uuid}")
        self.assertEqual(response.status_code, 200, response.content)

    def test_osmanager_roundtrip_is_accepted(self) -> None:
        osmanager = fixtures_services.create_db_osmanager()
        response = self._roundtrip(f"osmanagers/{osmanager.uuid}")
        self.assertEqual(response.status_code, 200, response.content)

    def test_servicepool_roundtrip_is_accepted(self) -> None:
        provider = fixtures_services.create_db_provider()
        service = fixtures_services.create_db_service(provider)
        pool = fixtures_services.create_db_servicepool(service)
        response = self._roundtrip(f"servicespools/{pool.uuid}")
        self.assertEqual(response.status_code, 200, response.content)

    def test_numeric_readonly_field_accepts_its_value_as_string(self) -> None:
        provider = self._proxmox_provider()
        item = self.client.rest_get(f"providers/{provider.uuid}").json()
        item["instance"]["start_vmid"] = str(item["instance"]["start_vmid"])
        response = self.client.rest_put(f"providers/{provider.uuid}", data=item)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self._stored_start_vmid(provider), 10000)

    def test_servicepool_base_service_is_not_changed(self) -> None:
        provider = fixtures_services.create_db_provider()
        service = fixtures_services.create_db_service(provider)
        other = fixtures_services.create_db_service(provider, use_caching_version=False)
        pool = fixtures_services.create_db_servicepool(service)
        item = self.client.rest_get(f"servicespools/{pool.uuid}").json()
        item["service_id"] = other.uuid
        self.client.rest_put(f"servicespools/{pool.uuid}", data=item)
        pool.refresh_from_db()
        self.assertEqual(pool.service.uuid, service.uuid)

    @unittest.expectedFailure
    def test_servicepool_account_is_readonly(self) -> None:
        provider = fixtures_services.create_db_provider()
        service = fixtures_services.create_db_service(provider)
        pool = fixtures_services.create_db_servicepool(service)
        account = models.Account.objects.create(name="An account")
        item = self.client.rest_get(f"servicespools/{pool.uuid}").json()
        item["account_id"] = account.uuid
        response = self.client.rest_put(f"servicespools/{pool.uuid}", data=item)
        self.assertEqual(response.status_code, 400, response.content)

    @unittest.expectedFailure
    def test_provider_numeric_readonly_field_is_readonly(self) -> None:
        provider = self._proxmox_provider()
        item = self.client.rest_get(f"providers/{provider.uuid}").json()
        item["instance"]["start_vmid"] = "10001"
        response = self.client.rest_put(f"providers/{provider.uuid}", data=item)
        self.assertEqual(response.status_code, 400, response.content)
