# -*- coding: utf-8 -*-
#
# Copyright (c) 2022 Virtual Cable S.L.
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
import logging
import typing

from uds import models
from ....fixtures import services as services_fixtures
from ....utils import rest

logger = logging.getLogger(__name__)


class ServicePoolTest(rest.test.RESTTestCase):
    @typing.override
    def setUp(self) -> None:
        # Override number of items to create
        super().setUp()
        self.login()

    def _pool_count_in_db(self) -> int:
        return models.ServicePool.objects.all().count()

    def _active_pool_count_in_db(self) -> int:
        """Count of pools NOT in REMOVABLE state (i.e. visible from the handler)."""
        from uds.core.types.states import State

        return (
            models.ServicePool.objects.all()
            .exclude(state=State.REMOVABLE)
            .count()
        )

    def _create_pool_payload(self, *, name: str = 'smoke-test-pool') -> dict[str, typing.Any]:
        """Build a minimal valid payload to create a ServicePool via PUT.

        Requires a real service_id; we create a fresh Service from the existing
        provider so the pool has a valid parent.

        Note: optional uuids (image_id, pool_group_id) use the sentinel '-1'
        that the handler treats as "none" (see services_pools.py pre_save).
        Leaving them as empty strings raises ValueError in process_uuid.
        """
        service = services_fixtures.create_db_service(self.provider)
        return {
            'name': name,
            'short_name': name,
            'comments': 'created by CRUD smoke test',
            'tags': [],
            'service_id': service.uuid,
            'osmanager_id': '-1',
            'image_id': '-1',
            'pool_group_id': '-1',
            'initial_srvs': 0,
            'cache_l1_srvs': 0,
            'cache_l2_srvs': 0,
            'max_srvs': 1,
            'show_transports': True,
            'visible': True,
            'allow_users_remove': False,
            'allow_users_reset': False,
            'ignores_unused': False,
            'account_id': '-1',
            'calendar_message': '',
            'custom_message': '',
            'display_custom_message': False,
        }

    # ------------------------------------------------------------------
    # Existing tests (preserved from before the CRUD extension)
    # ------------------------------------------------------------------
    def test_invalid_servicepool(self) -> None:
        url = f'servicespools/INVALID/overview'

        response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 404)

    def test_service_pools(self) -> None:
        url = f'servicespools/overview'

        # Now, will work
        response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 200)
        # Get the list of service pools from DB
        db_pools_len = models.ServicePool.objects.all().count()
        re_pools: list[dict[str, typing.Any]] = response.json()

        self.assertIsInstance(re_pools, list)
        self.assertEqual(db_pools_len, len(re_pools))

        for service_pool in re_pools:
            # Get from DB the service pool
            db_pool = models.ServicePool.objects.get(uuid=service_pool['id'])
            self.assertTrue(rest.assertions.assert_servicepool_is(db_pool, service_pool))

    # ------------------------------------------------------------------
    # CRUD smoke extension (Phase 1 — Safety net)
    # ------------------------------------------------------------------
    def test_get_nonexistent_item_returns_404(self) -> None:
        """GET /servicespools/<nonexistent-uuid> returns 404."""
        response = self.client.rest_get('servicespools/00000000-0000-0000-0000-000000000000')
        self.assertEqual(response.status_code, 404)

    def test_put_creates_new_service_pool(self) -> None:
        """PUT /servicespools (no ID) creates a new ServicePool."""
        before = self._pool_count_in_db()
        payload = self._create_pool_payload(name='smoke-create')
        response = self.client.rest_put('servicespools', data=payload)
        self.assertEqual(response.status_code, 200, response.content)
        item: dict[str, typing.Any] = response.json()
        self.assertIn('id', item)
        self.assertEqual(item['name'], 'smoke-create')

        after = self._pool_count_in_db()
        self.assertEqual(after, before + 1, 'PUT create must add exactly one ServicePool')
        self.assertTrue(models.ServicePool.objects.filter(uuid=item['id']).exists())

    def test_put_updates_existing_service_pool(self) -> None:
        """PUT /servicespools/<uuid> updates an existing ServicePool."""
        create_resp = self.client.rest_put('servicespools', data=self._create_pool_payload(name='before'))
        self.assertEqual(create_resp.status_code, 200, create_resp.content)
        new_uuid: str = create_resp.json()['id']

        update_payload = self._create_pool_payload(name='after')
        update_payload['comments'] = 'cambiado'
        update_resp = self.client.rest_put(f'servicespools/{new_uuid}', data=update_payload)
        self.assertEqual(update_resp.status_code, 200, update_resp.content)
        updated: dict[str, typing.Any] = update_resp.json()
        self.assertEqual(updated['id'], new_uuid)
        self.assertEqual(updated['name'], 'after')

        db_pool = models.ServicePool.objects.get(uuid=new_uuid)
        self.assertEqual(db_pool.name, 'after')
        self.assertEqual(db_pool.comments, 'cambiado')

    def test_delete_service_pool_is_soft(self) -> None:
        """DELETE /servicespools/<uuid> marks the pool as REMOVABLE (soft delete).

        Contract (see src/uds/models/service_pool.py:451 `remove()` and
        src/uds/REST/methods/services_pools.py:646 `delete_item`):
        DELETE does NOT remove the row; it marks state as REMOVABLE and lets
        the background worker physically remove it. The handler hides
        REMOVABLE pools from listing.

        Behavior we freeze with this test:
        - DELETE returns 200 'ok'.
        - Visible pool count goes down by 1.
        - DB row count is unchanged (still there with state=REMOVABLE).
        """
        from uds.core.types.states import State

        create_resp = self.client.rest_put('servicespools', data=self._create_pool_payload(name='to-delete'))
        new_uuid: str = create_resp.json()['id']

        visible_before = self._active_pool_count_in_db()
        total_before = self._pool_count_in_db()

        delete_resp = self.client.rest_delete(f'servicespools/{new_uuid}')
        self.assertEqual(delete_resp.status_code, 200, delete_resp.content)
        self.assertEqual(delete_resp.json(), 'ok')

        # Visible (non-REMOVABLE) count must drop by exactly 1
        self.assertEqual(
            self._active_pool_count_in_db(),
            visible_before - 1,
            'DELETE on ServicePool must reduce the active (non-REMOVABLE) count by 1',
        )

        # The row may still exist in the DB, but in REMOVABLE state
        db_pool = models.ServicePool.objects.filter(uuid=new_uuid).first()
        self.assertIsNotNone(
            db_pool,
            'Soft-delete semantics: row may remain in DB, marked REMOVABLE',
        )
        assert db_pool is not None  # for type checkers
        self.assertEqual(
            db_pool.state,
            State.REMOVABLE,
            'Soft-delete semantics: state must be REMOVABLE after DELETE',
        )
        # Total row count is unchanged
        self.assertEqual(
            self._pool_count_in_db(),
            total_before,
            'Soft-delete semantics: total row count must NOT change after DELETE',
        )

    def test_get_after_delete_shows_removable_state(self) -> None:
        """After DELETE, GET of the same uuid returns 200 with state=REMOVABLE.

        Contract note: ServicePool DELETE is a soft delete (sets state=REMOVABLE).
        The pool row remains in the DB and is STILL readable via GET /servicespools/<uuid>,
        with the ``state`` field reporting 'R'. This is by design — see
        src/uds/models/service_pool.py:451 (remove()).

        Note: this is a deliberate departure from the typical "404 after delete"
        behavior of other handlers (e.g. providers, authenticators). It must be
        preserved across future changes (Phase 4 migration of GET-modifiers, etc.).
        """
        from uds.core.types.states import State

        create_resp = self.client.rest_put('servicespools', data=self._create_pool_payload(name='to-delete-3'))
        new_uuid: str = create_resp.json()['id']

        self.client.rest_delete(f'servicespools/{new_uuid}')

        # GET of the soft-deleted pool returns 200, not 404 (because row is in DB)
        response = self.client.rest_get(f'servicespools/{new_uuid}')
        self.assertEqual(response.status_code, 200, response.content)
        item: dict[str, typing.Any] = response.json()
        self.assertEqual(item['id'], new_uuid)
        self.assertEqual(
            item['state'],
            State.REMOVABLE,
            'After DELETE, GET must report state=REMOVABLE (soft-delete contract).',
        )
