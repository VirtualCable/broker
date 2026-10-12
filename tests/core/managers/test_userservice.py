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

    def test_get_user_service_skips_cache_of_old_publication_revision(self) -> None:
        """Cache user services of an older publication revision must not be assigned
        to new users: ``current_pub_revision`` already advanced, so the alternate
        publish method (ALT_LOCKED_PUBLISH_METHOD) left the old cache alive but
        the assignment filter must exclude it. The new cache (revision == current)
        is the only valid candidate.
        """
        from tests.fixtures import authenticators as auth_fixtures

        provider = services_fixtures.create_db_provider()
        service = services_fixtures.create_db_service(provider)
        osmanager = services_fixtures.create_db_osmanager()
        groups = auth_fixtures.create_db_groups(auth_fixtures.create_db_authenticator(), 1)
        transport = services_fixtures.create_db_transport()
        service_pool = services_fixtures.create_db_servicepool(service, osmanager, groups, [transport])

        # Simulate post-publish state: pool is at N+1, two USABLE publications exist
        # because ALT_LOCKED_PUBLISH_METHOD left the old one alive.
        service_pool.current_pub_revision = 2
        service_pool.initial_srvs = 2
        service_pool.cache_l1_srvs = 0
        service_pool.cache_l2_srvs = 0
        service_pool.max_srvs = 2
        service_pool.save()

        old_publication = service_pool.publications.create(
            revision=1,
            publish_date=timezone.localtime() - datetime.timedelta(hours=1),
            state=core_types.states.State.USABLE,
            state_date=timezone.localtime() - datetime.timedelta(hours=1),
        )
        new_publication = service_pool.publications.create(
            revision=2,
            publish_date=timezone.localtime(),
            state=core_types.states.State.USABLE,
            state_date=timezone.localtime(),
        )

        old_cache = services_fixtures.create_db_userservice(service_pool, old_publication)
        old_cache.cache_level = core_types.services.CacheLevel.L1
        old_cache.user = None
        old_cache.os_state = core_types.states.State.USABLE
        old_cache.save()

        new_cache = services_fixtures.create_db_userservice(service_pool, new_publication)
        new_cache.cache_level = core_types.services.CacheLevel.L1
        new_cache.user = None
        new_cache.os_state = core_types.states.State.USABLE
        new_cache.save()

        # Fresh user that has no assigned user service yet
        from tests.fixtures import authenticators as auth_fixtures

        auth = auth_fixtures.create_db_authenticator()
        user_groups = auth_fixtures.create_db_groups(auth, 1)
        user = auth_fixtures.create_db_users(auth, 1, groups=user_groups)[0]

        assigned = self.manager.get_assignation_for_user(service_pool, user)
        # Must get the cache of the *new* publication (revision == current_pub_revision),
        # not the old one (revision 1).
        self.assertIsNotNone(assigned)
        self.assertEqual(assigned.uuid, new_cache.uuid)
        self.assertEqual(assigned.publication_id, new_publication.id)
        self.assertEqual(assigned.cache_level, core_types.services.CacheLevel.NONE)
        self.assertEqual(assigned.user_id, user.id)

    def test_get_user_service_no_cache_when_publication_not_published_yet(self) -> None:
        """During the publish window ``current_pub_revision`` is bumped but the new
        publication is not yet USABLE. The cache filter yields no candidate and the
        manager falls through to fresh-assignment, leaving the old cache untouched.
        """
        from tests.fixtures import authenticators as auth_fixtures

        provider = services_fixtures.create_db_provider()
        service = services_fixtures.create_db_service(provider)
        osmanager = services_fixtures.create_db_osmanager()
        groups = auth_fixtures.create_db_groups(auth_fixtures.create_db_authenticator(), 1)
        transport = services_fixtures.create_db_transport()
        service_pool = services_fixtures.create_db_servicepool(service, osmanager, groups, [transport])

        # current_pub_revision is N+1 but no USABLE publication has that revision yet
        service_pool.current_pub_revision = 2
        service_pool.initial_srvs = 1
        service_pool.cache_l1_srvs = 0
        service_pool.cache_l2_srvs = 0
        service_pool.max_srvs = 2
        service_pool.save()

        old_publication = service_pool.publications.create(
            revision=1,
            publish_date=timezone.localtime() - datetime.timedelta(hours=1),
            state=core_types.states.State.USABLE,
            state_date=timezone.localtime() - datetime.timedelta(hours=1),
        )
        # New publication in PREPARING (publish in flight, not yet USABLE)
        service_pool.publications.create(
            revision=2,
            publish_date=timezone.localtime(),
            state=core_types.states.State.PREPARING,
            state_date=timezone.localtime(),
        )

        old_cache = services_fixtures.create_db_userservice(service_pool, old_publication)
        old_cache.cache_level = core_types.services.CacheLevel.L1
        old_cache.user = None
        old_cache.os_state = core_types.states.State.USABLE
        old_cache.save()

        from tests.fixtures import authenticators as auth_fixtures

        auth = auth_fixtures.create_db_authenticator()
        user_groups = auth_fixtures.create_db_groups(auth, 1)
        user = auth_fixtures.create_db_users(auth, 1, groups=user_groups)[0]

        # Old cache must remain untouched (its user still None)
        assigned = self.manager.get_assignation_for_user(service_pool, user)
        # No cache match → fresh assigned creation (cache_level=NONE, not the old cache)
        if assigned is not None:
            self.assertNotEqual(assigned.uuid, old_cache.uuid)
        # Old cache must remain free (user is None) so the 24h alternate window still works
        old_cache.refresh_from_db()
        self.assertIsNone(old_cache.user)
        self.assertTrue(old_cache.is_usable())

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
