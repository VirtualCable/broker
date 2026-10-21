#
# Copyright (c) 2024 Virtual Cable S.L.
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
Author: Adolfo Gómez, dkmaster at dkmon dot com
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import datetime
import logging
from unittest import mock

from django.utils import timezone

from tests.fixtures import services as services_fixtures
from tests.utils.test import UDSTransactionTestCase
from uds import models
from uds.core import types as core_types
from uds.core.managers.userservice import UserServiceManager

logger: logging.Logger = logging.getLogger(__name__)


class TestUserserviceManager(UDSTransactionTestCase):
    manager: UserServiceManager = UserServiceManager.manager()  # For convenience debugging

    def test_forced_mode_assigned_to_l1(self) -> None:
        # Create an user service, we need
        userservice = services_fixtures.create_db_assigned_userservices()[0]

        orig_uuid = userservice.uuid
        orig_src_ip = userservice.src_ip
        orig_src_hostname = userservice.src_hostname

        self.assertEqual(models.UserService.objects.all().count(), 1)
        # And uuser service is assigned to an user
        self.assertIsNotNone(userservice.user)
        # And cache level is None
        self.assertEqual(userservice.cache_level, core_types.services.CacheLevel.NONE)

        self.manager.forced_move_assigned_to_cache_l1(userservice)

        # Now, should have 2 user services, one in cache and one in db
        self.assertEqual(models.UserService.objects.all().count(), 2)
        # Reload userservice, that should be now in cache
        userservice = models.UserService.objects.get(uuid=orig_uuid)
        self.assertEqual(userservice.cache_level, core_types.services.CacheLevel.L1)
        # Should have no user assigned
        self.assertIsNone(userservice.user)
        # Should be usable
        self.assertTrue(userservice.is_usable())
        # Should not be in use
        self.assertFalse(userservice.in_use)
        # Source ip and hostname should be empty
        self.assertEqual(userservice.src_ip, "")
        self.assertEqual(userservice.src_hostname, "")

        # Look for the created one (that is the assigned, deleted)
        assigned = models.UserService.objects.exclude(uuid=orig_uuid).get()
        self.assertEqual(assigned.cache_level, core_types.services.CacheLevel.NONE)
        # Should have the user assigned
        self.assertIsNotNone(assigned.user)
        # Should be removed
        self.assertEqual(assigned.state, core_types.states.State.REMOVED)

        # unique_id should be same as the original one
        self.assertEqual(userservice.unique_id, assigned.unique_id)
        # src_ip and src_hostname should be the original ones
        self.assertEqual(assigned.src_ip, orig_src_ip)
        self.assertEqual(assigned.src_hostname, orig_src_hostname)

    def test_release_from_logout(self) -> None:
        pass

    def test_get_user_service_with_initial_no_cache(self) -> None:
        userservice = services_fixtures.create_db_assigned_userservices()[0]
        user = userservice.user
        assert user is not None

        # Fix userservice to set it as cache l1
        userservice.cache_level = core_types.services.CacheLevel.L1
        userservice.user = None
        userservice.save()

        service_pool = userservice.service_pool

        service_pool.initial_srvs = 1
        service_pool.cache_l1_srvs = 0
        service_pool.cache_l2_srvs = 0
        service_pool.max_srvs = 3
        service_pool.save()

        assigned_info = self.manager.get_user_service_info(
            user,
            core_types.os.DetectedOsInfo(
                core_types.os.KnownOS.LINUX, core_types.os.KnownBrowser.CHROME, "1.0.0"
            ),
            "1.2.3.4",
            f"P{service_pool.uuid}",
            service_pool.transports.all()[0].uuid,
        )

        self.assertEqual(assigned_info.userservice.uuid, userservice.uuid)
        self.assertEqual(assigned_info.userservice.cache_level, core_types.services.CacheLevel.NONE)
        self.assertEqual(assigned_info.userservice.user, user)
        self.assertEqual(assigned_info.userservice.src_ip, "1.2.3.4")
        self.assertEqual(assigned_info.userservice.src_hostname, "1.2.3.4")
        self.assertEqual(assigned_info.transport.uuid, service_pool.transports.all()[0].uuid)

    def _create_cacheable_pool(self) -> models.ServicePool:
        from tests.fixtures import authenticators as auth_fixtures

        provider = services_fixtures.create_db_provider()
        service = services_fixtures.create_db_service(provider)
        osmanager = services_fixtures.create_db_osmanager()
        groups = auth_fixtures.create_db_groups(auth_fixtures.create_db_authenticator(), 1)
        transport = services_fixtures.create_db_transport()
        service_pool = services_fixtures.create_db_servicepool(service, osmanager, groups, [transport])
        service_pool.initial_srvs = 0
        service_pool.cache_l1_srvs = 2
        service_pool.cache_l2_srvs = 0
        service_pool.max_srvs = 4
        service_pool.save()
        return service_pool

    def _publish(
        self, service_pool: models.ServicePool, state: core_types.states.State
    ) -> models.ServicePoolPublication:
        # Same revision bookkeeping as PublicationManager.publish + PublicationLauncher.run
        publication = service_pool.publications.create(
            revision=service_pool.current_pub_revision,
            publish_date=timezone.localtime(),
            state=state,
            state_date=timezone.localtime(),
        )
        service_pool.current_pub_revision += 1
        service_pool.save()
        return publication

    def _create_l1_cache(
        self,
        service_pool: models.ServicePool,
        publication: models.ServicePoolPublication,
        state: core_types.states.State = core_types.states.State.USABLE,
        os_state: core_types.states.State = core_types.states.State.USABLE,
    ) -> models.UserService:
        cache = services_fixtures.create_db_userservice(service_pool, publication)
        cache.cache_level = core_types.services.CacheLevel.L1
        cache.state = state
        cache.os_state = os_state
        cache.save()
        return cache

    def _create_user(self) -> models.User:
        from tests.fixtures import authenticators as auth_fixtures

        auth = auth_fixtures.create_db_authenticator()
        return auth_fixtures.create_db_users(auth, 1, groups=auth_fixtures.create_db_groups(auth, 1))[0]

    def assert_cache_assigned(
        self, assigned: models.UserService | None, cache: models.UserService, user: models.User
    ) -> None:
        self.assertIsNotNone(assigned)
        assert assigned is not None
        self.assertEqual(assigned.uuid, cache.uuid)
        self.assertEqual(assigned.cache_level, core_types.services.CacheLevel.NONE)
        self.assertEqual(assigned.user, user)
        self.assertEqual(models.UserService.objects.filter(user=user).count(), 1)

    def test_get_user_service_assigns_ready_cache_of_active_publication(self) -> None:
        service_pool = self._create_cacheable_pool()
        publication = self._publish(service_pool, core_types.states.State.USABLE)
        cache = self._create_l1_cache(service_pool, publication)
        user = self._create_user()

        assigned = self.manager.get_assignation_for_user(service_pool, user)

        self.assert_cache_assigned(assigned, cache, user)
        self.assertEqual(models.UserService.objects.count(), 1)

    def test_get_user_service_assigns_cache_of_active_publication_with_os_not_ready(self) -> None:
        service_pool = self._create_cacheable_pool()
        publication = self._publish(service_pool, core_types.states.State.USABLE)
        cache = self._create_l1_cache(service_pool, publication, os_state=core_types.states.State.PREPARING)
        user = self._create_user()

        assigned = self.manager.get_assignation_for_user(service_pool, user)

        self.assert_cache_assigned(assigned, cache, user)
        self.assertEqual(models.UserService.objects.count(), 1)

    def test_get_user_service_assigns_preparing_cache_of_active_publication(self) -> None:
        service_pool = self._create_cacheable_pool()
        publication = self._publish(service_pool, core_types.states.State.USABLE)
        cache = self._create_l1_cache(
            service_pool,
            publication,
            state=core_types.states.State.PREPARING,
            os_state=core_types.states.State.PREPARING,
        )
        user = self._create_user()

        with mock.patch.object(self.manager, "check_user_service_uuid", return_value=True):
            assigned = self.manager.get_assignation_for_user(service_pool, user)

        self.assert_cache_assigned(assigned, cache, user)
        self.assertEqual(models.UserService.objects.count(), 1)

    def test_get_user_service_skips_cache_of_replaced_publication(self) -> None:
        # ALT_LOCKED_PUBLISH_METHOD keeps the cache of the replaced publication alive
        service_pool = self._create_cacheable_pool()
        old_publication = self._publish(service_pool, core_types.states.State.REMOVABLE)
        new_publication = self._publish(service_pool, core_types.states.State.USABLE)
        old_cache = self._create_l1_cache(service_pool, old_publication)
        new_cache = self._create_l1_cache(service_pool, new_publication)
        user = self._create_user()

        assigned = self.manager.get_assignation_for_user(service_pool, user)

        self.assert_cache_assigned(assigned, new_cache, user)
        old_cache.refresh_from_db()
        self.assertIsNone(old_cache.user)
        self.assertEqual(old_cache.cache_level, core_types.services.CacheLevel.L1)

    def test_get_user_service_never_assigns_cache_of_replaced_publication(self) -> None:
        service_pool = self._create_cacheable_pool()
        old_publication = self._publish(service_pool, core_types.states.State.REMOVABLE)
        new_publication = self._publish(service_pool, core_types.states.State.USABLE)
        old_cache = self._create_l1_cache(service_pool, old_publication)
        user = self._create_user()

        assigned = self.manager.get_assignation_for_user(service_pool, user)

        self.assertIsNotNone(assigned)
        assert assigned is not None
        self.assertNotEqual(assigned.uuid, old_cache.uuid)
        self.assertEqual(assigned.publication, new_publication)
        old_cache.refresh_from_db()
        self.assertIsNone(old_cache.user)
        self.assertEqual(old_cache.cache_level, core_types.services.CacheLevel.L1)

    def test_get_user_service_assigns_cache_of_active_publication_while_publishing(self) -> None:
        # Until the new publication finishes, the previous one is still the active one
        service_pool = self._create_cacheable_pool()
        active_publication = self._publish(service_pool, core_types.states.State.USABLE)
        self._publish(service_pool, core_types.states.State.PREPARING)
        cache = self._create_l1_cache(service_pool, active_publication)
        user = self._create_user()

        assigned = self.manager.get_assignation_for_user(service_pool, user)

        self.assert_cache_assigned(assigned, cache, user)

    def test_get_user_service_with_usable_cache_and_os_not_ready(self) -> None:
        userservice = services_fixtures.create_db_assigned_userservices()[0]
        user = userservice.user
        assert user is not None

        userservice.cache_level = core_types.services.CacheLevel.L1
        userservice.user = None
        userservice.state = core_types.states.State.USABLE
        userservice.os_state = core_types.states.State.PREPARING
        userservice.save()

        service_pool = userservice.service_pool

        service_pool.initial_srvs = 1
        service_pool.cache_l1_srvs = 0
        service_pool.cache_l2_srvs = 0
        service_pool.max_srvs = 3
        service_pool.save()

        assigned = self.manager.get_assignation_for_user(service_pool, user)

        self.assertIsNotNone(assigned)
        assert assigned is not None
        self.assertEqual(assigned.uuid, userservice.uuid)
        self.assertEqual(assigned.cache_level, core_types.services.CacheLevel.NONE)
        self.assertEqual(service_pool.assigned_user_services().filter(user=user).count(), 1)

    def test_get_assignation_assigns_a_single_userservice_when_cache_os_is_not_ready(self) -> None:
        """A cache hit on a service whose os is still preparing is resolved by the second
        lookup, which already reserves the row on database. Falling through to the
        "cache missed" lookups from there reserved a second user service for the same
        user, leaving the first one assigned and unreachable.
        """
        from tests.fixtures import authenticators as auth_fixtures

        provider = services_fixtures.create_db_provider()
        service = services_fixtures.create_db_service(provider)
        osmanager = services_fixtures.create_db_osmanager()
        authenticator = auth_fixtures.create_db_authenticator()
        groups = auth_fixtures.create_db_groups(authenticator, 1)
        user = auth_fixtures.create_db_users(authenticator, 1, groups=groups)[0]
        transport = services_fixtures.create_db_transport()
        service_pool = services_fixtures.create_db_servicepool(service, osmanager, groups, [transport])

        service_pool.initial_srvs = 0
        service_pool.cache_l1_srvs = 2
        service_pool.cache_l2_srvs = 0
        service_pool.max_srvs = 4
        service_pool.save()

        publication = services_fixtures.create_db_publication(service_pool)

        usable_cache = services_fixtures.create_db_userservice(service_pool, publication)
        usable_cache.cache_level = core_types.services.CacheLevel.L1
        usable_cache.os_state = core_types.states.State.PREPARING
        usable_cache.save()

        preparing_cache = services_fixtures.create_db_userservice(service_pool, publication)
        preparing_cache.cache_level = core_types.services.CacheLevel.L1
        preparing_cache.state = core_types.states.State.PREPARING
        preparing_cache.os_state = core_types.states.State.PREPARING
        preparing_cache.save()

        assigned = self.manager.get_assignation_for_user(service_pool, user)

        self.assertIsNotNone(assigned)
        assert assigned is not None
        self.assertEqual(assigned.uuid, usable_cache.uuid)
        self.assertEqual(assigned.cache_level, core_types.services.CacheLevel.NONE)
        self.assertEqual(assigned.user, user)
        self.assertEqual(models.UserService.objects.filter(user=user).count(), 1)

        preparing_cache.refresh_from_db()
        self.assertIsNone(preparing_cache.user)
        self.assertEqual(preparing_cache.cache_level, core_types.services.CacheLevel.L1)

    def test_check_user_service_uuid(self) -> None:
        userservice = services_fixtures.create_db_assigned_userservices()[0]
        actor_token = models.user_service.create_actor_token()
        userservice.token_hash = models.user_service.hash_actor_token(actor_token)
        userservice.save(update_fields=["token_hash"])

        for actor_answer, expected in (
            (actor_token, True),  # Actor registered against a 5.0 server
            (userservice.uuid, True),  # Actor registered against a 4.0 server, upgraded afterwards
            ("", True),  # Unmanaged/fixed machine, no check at all
            (models.user_service.create_actor_token(), False),  # Machine replaced behind our back
        ):
            with mock.patch(
                "uds.core.managers.userservice_helpers.comms._execute_actor_request",
                return_value=actor_answer,
            ):
                self.assertEqual(self.manager.check_user_service_uuid(userservice), expected, actor_answer)
