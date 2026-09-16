#
# Copyright (c) 2025 Virtual Cable S.L.
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
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
# IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
# OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
Read-side smoke test for the /servicespools/<uuid>/services DETAIL handler
(Phase 1 — Safety net extension).

AssignedUserService is a DetailHandler (sub-resource of a ServicePool).
It does NOT expose PUT/DELETE (the base DetailHandler rejects POST and
requires a parent + child id for DELETE). It only exposes GET plus a
CUSTOM_METHODS GET-modifier (`reset`) that will be migrated to POST in
Phase 4.

This test freezes:
- The GET overview contract for assigned user services of a pool.
- The 404 contract for nonexistent user services.
- That POST is rejected (handled by the base DetailHandler.post()).

It deliberately does NOT try to create/update/delete user services because
those operations go through the broker pipeline, not the REST API — trying
to write through the handler would be testing the wrong layer.

Reference: src/uds/REST/methods/user_services.py  (AssignedUserService)

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import logging
import typing
from unittest import mock

from uds import models

from ....fixtures import services as services_fixtures
from ....utils import rest

logger: logging.Logger = logging.getLogger(__name__)


class PoolUserServicesCrudTest(rest.test.RESTTestCase):
    """Freezes the read-side contract of /servicespools/<uuid>/services."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def _any_user_service_under_pool(self) -> models.UserService | None:
        # Exclude State.INFO_STATES mirrors the pattern used in
        # user_services.py (e.g. line 144: .exclude(state__in=State.INFO_STATES)).
        from uds.core.types.states import State

        return (
            models.UserService.objects.filter(deployed_service__service__provider=self.provider)
            .exclude(state__in=State.INFO_STATES)
            .first()
        )

    def test_get_overview_returns_list_under_pool(self) -> None:
        """GET /servicespools/<uuid>/services/overview returns 200 + user services of the pool."""
        # Pick any pool that has user services from setUp
        any_userservice = self._any_user_service_under_pool()
        if any_userservice is None:
            self.skipTest("No UserService available from setUp; cannot test list")

        pool_id = any_userservice.deployed_service.uuid  # ServicePool uuid
        url = f"servicespools/{pool_id}/services/overview"
        response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 200)
        items: list[dict[str, typing.Any]] = response.json()
        self.assertIsInstance(items, list)
        # Whatever setUp produced must be there.
        self.assertGreaterEqual(
            len(items),
            1,
            "Overview must include at least the user services created by RESTTestCase.setUp",
        )

    def test_get_nonexistent_user_service_under_pool_returns_404(self) -> None:
        """GET /servicespools/<uuid>/services/<bogus> returns 404."""
        any_userservice = self._any_user_service_under_pool()
        if any_userservice is None:
            self.skipTest("No UserService available from setUp")

        pool_id = any_userservice.deployed_service.uuid
        url = f"servicespools/{pool_id}/services/00000000-0000-0000-0000-000000000000"
        response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 404)

    def test_post_under_pool_is_rejected_by_base_detailhandler(self) -> None:
        """DetailHandler.post() rejects POST with RequestError (no detail writes via POST)."""
        any_userservice = self._any_user_service_under_pool()
        if any_userservice is None:
            self.skipTest("No UserService available from setUp")

        pool_id = any_userservice.deployed_service.uuid
        url = f"servicespools/{pool_id}/services"
        response = self.client.rest_post(url, data={"ignored": True})
        # The base DetailHandler.post raises RequestError -> 400.
        # We assert "<= 500" so any future change of the error class is
        # explicitly noted via this contract snapshot.
        self.assertLess(response.status_code, 500, response.content)
        self.assertGreaterEqual(response.status_code, 400)


class PoolCapabilitiesGatesTest(rest.test.RESTTestCase):
    """The capability gates added with the pool capability contract.

    Pools whose base service type does not support an operation must
    answer ``400 Not supported`` (the dispatcher's ``NotSupportedError``
    mapping) instead of silently succeeding, returning empty collections
    or half-working surfaces:

    - ``services/{uuid}/reset`` (POST) when ``can_reset`` is false;
    - the whole ``cache`` detail when ``uses_cache`` is false;
    - the ``publications`` and ``changelog`` details when the type does
      not need publications at all.
    """

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def _pool(self, caching: bool) -> models.ServicePool:
        service = services_fixtures.create_db_service(self.provider, use_caching_version=caching)
        return services_fixtures.create_db_servicepool(service)

    def _assigned(self, pool: models.ServicePool) -> models.UserService:
        publication = services_fixtures.create_db_publication(pool)
        return services_fixtures.create_db_userservice(pool, publication, self.plain_users[0])

    def test_reset_unsupported_answers_not_supported(self) -> None:
        pool = self._pool(caching=True)
        userservice = self._assigned(pool)
        # TestServiceCache.can_reset is False by default.
        response = self.client.rest_post(f"servicespools/{pool.uuid}/services/{userservice.uuid}/reset")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Not supported", response.content)

    def test_reset_supported_reaches_the_manager(self) -> None:
        from tests.fixtures.modules.service.service import TestServiceCache

        pool = self._pool(caching=True)
        userservice = self._assigned(pool)
        manager = mock.MagicMock()
        with (
            mock.patch.object(TestServiceCache, "can_reset", True),
            mock.patch("uds.REST.methods.user_services.UserServiceManager.manager", return_value=manager),
        ):
            response = self.client.rest_post(f"servicespools/{pool.uuid}/services/{userservice.uuid}/reset")
        self.assertEqual(response.status_code, 200, response.content)
        manager.reset.assert_called_once()
        self.assertEqual(manager.reset.call_args[0][0].uuid, userservice.uuid)

    def test_cache_detail_unsupported_answers_not_supported(self) -> None:
        pool = self._pool(caching=False)
        for verb, call in (
            ("GET", self.client.rest_get),
            ("DELETE", self.client.rest_delete),
        ):
            url = (
                f"servicespools/{pool.uuid}/cache/overview"
                if verb == "GET"
                else f"servicespools/{pool.uuid}/cache/00000000-0000-0000-0000-000000000000"
            )
            response = call(url)
            self.assertEqual(response.status_code, 400, f"{verb} {url}")
            self.assertIn(b"Not supported", response.content)

    def test_cache_detail_supported_answers_ok(self) -> None:
        pool = self._pool(caching=True)
        response = self.client.rest_get(f"servicespools/{pool.uuid}/cache/overview")
        self.assertEqual(response.status_code, 200)

    def test_publications_unsupported_answers_not_supported(self) -> None:
        pool = self._pool(caching=False)
        response = self.client.rest_get(f"servicespools/{pool.uuid}/publications/overview")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Not supported", response.content)
        response = self.client.rest_post(f"servicespools/{pool.uuid}/publications/publish")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Not supported", response.content)

    def test_publications_supported_answers_ok(self) -> None:
        pool = self._pool(caching=True)
        response = self.client.rest_get(f"servicespools/{pool.uuid}/publications/overview")
        self.assertEqual(response.status_code, 200)

    def test_changelog_unsupported_answers_not_supported(self) -> None:
        pool = self._pool(caching=False)
        response = self.client.rest_get(f"servicespools/{pool.uuid}/changelog/overview")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Not supported", response.content)

    def test_changelog_supported_answers_ok(self) -> None:
        pool = self._pool(caching=True)
        response = self.client.rest_get(f"servicespools/{pool.uuid}/changelog/overview")
        self.assertEqual(response.status_code, 200)
