"""
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

from uds import models
from uds.REST import log as rest_log

from ..fixtures import services as services_fixtures
from ..utils.test import UDSTestCase

UNKNOWN_UUID = '00000000-0000-0000-0000-000000000000'


class ReplacePathTest(UDSTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.provider = services_fixtures.create_db_provider()
        self.service = services_fixtures.create_db_service(self.provider)
        self.pool = services_fixtures.create_db_servicepool(self.service)
        publication = services_fixtures.create_db_publication(self.pool)
        self.userservice = services_fixtures.create_db_userservice(self.pool, publication)

    def test_pool_assigned_userservice_is_named(self) -> None:
        path = f'/uds/rest/servicespools/{self.pool.uuid}/services/{self.userservice.uuid}'
        self.assertEqual(
            rest_log.replace_path(path),
            f'/uds/rest/servicespools/[{self.pool.name}]/services/[{self.userservice.friendly_name}]',
        )

    def test_pool_cached_userservice_is_named(self) -> None:
        path = f'/uds/rest/servicespools/{self.pool.uuid}/cache/{self.userservice.uuid}'
        self.assertEqual(
            rest_log.replace_path(path),
            f'/uds/rest/servicespools/[{self.pool.name}]/cache/[{self.userservice.friendly_name}]',
        )

    def test_metapool_assigned_userservice_is_named(self) -> None:
        metapool = services_fixtures.create_db_metapool([self.pool], [])
        path = f'/uds/rest/metapools/{metapool.uuid}/services/{self.userservice.uuid}'
        self.assertEqual(
            rest_log.replace_path(path),
            f'/uds/rest/metapools/[{metapool.name}]/services/[{self.userservice.friendly_name}]',
        )

    def test_provider_service_is_still_named(self) -> None:
        path = f'/uds/rest/providers/{self.provider.uuid}/services/{self.service.uuid}'
        self.assertEqual(
            rest_log.replace_path(path),
            f'/uds/rest/providers/[{self.provider.name}]/services/[{self.service.name}]',
        )

    def test_unknown_uuid_is_left_untouched(self) -> None:
        path = f'/uds/rest/servicespools/{self.pool.uuid}/services/{UNKNOWN_UUID}'
        self.assertEqual(
            rest_log.replace_path(path),
            f'/uds/rest/servicespools/[{self.pool.name}]/services/{UNKNOWN_UUID}',
        )

    def test_notifier_is_named(self) -> None:
        notifier = models.Notifier.objects.create(name='events webhook', data_type='webhook')
        path = f'/uds/rest/messaging/notifiers/{notifier.uuid}'
        self.assertEqual(
            rest_log.replace_path(path),
            f'/uds/rest/messaging/notifiers/[{notifier.name}]',
        )

    def test_gallery_image_is_named(self) -> None:
        image = models.Image.objects.create(name='icon', data=b'', thumb=b'')
        path = f'/uds/rest/gallery/images/{image.uuid}'
        self.assertEqual(
            rest_log.replace_path(path),
            f'/uds/rest/gallery/images/[{image.name}]',
        )
