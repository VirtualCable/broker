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

import datetime
import logging
import typing

from django.utils import timezone

from uds import models
from uds.core import types
from uds.core.consts import predictions as pred_consts
from uds.core.util.cache import Cache
from uds.core.types.states import State

from tests.fixtures import services as services_fixtures
from tests.utils import rest

logger: logging.Logger = logging.getLogger(__name__)


class ServicePoolTest(rest.test.RESTTestCase):
    @typing.override
    def setUp(self) -> None:
        # Override number of items to create
        super().setUp()
        self.login()

    def _create_pool_for_fallback_tests(self) -> models.ServicePool:
        """Create a real ServicePool via the public REST API (PUT create).

        Going through the REST API mirrors the path real clients use and
        gives us the canonical ``id`` they would call the custom method on.
        """
        payload = self._create_pool_payload(name="pool-for-fallback-tests")
        resp = self.client.rest_put("servicespools", data=payload)
        assert resp.status_code == 200, resp.content  # help test debugging
        return models.ServicePool.objects.get(uuid=resp.json()["id"])

    def _pool_count_in_db(self) -> int:
        return models.ServicePool.objects.all().count()

    def _active_pool_count_in_db(self) -> int:
        """Count of pools NOT in REMOVABLE state (i.e. visible from the handler)."""
        from uds.core.types.states import State

        return models.ServicePool.objects.all().exclude(state=State.REMOVABLE).count()

    def _create_pool_payload(self, *, name: str = "smoke-test-pool") -> dict[str, typing.Any]:
        """Build a minimal valid payload to create a ServicePool via PUT.

        Requires a real service_id; we create a fresh Service from the existing
        provider so the pool has a valid parent.

        Note: optional uuids (image_id, pool_group_id) use the sentinel '-1'
        that the handler treats as "none" (see services_pools.py pre_save).
        Leaving them as empty strings raises ValueError in process_uuid.
        """
        service = services_fixtures.create_db_service(self.provider)
        return {
            "name": name,
            "short_name": name,
            "comments": "created by CRUD smoke test",
            "tags": [],
            "service_id": service.uuid,
            "osmanager_id": "-1",
            "image_id": "-1",
            "pool_group_id": "-1",
            "initial_srvs": 0,
            "cache_l1_srvs": 0,
            "cache_l2_srvs": 0,
            "max_srvs": 1,
            "show_transports": True,
            "visible": True,
            "allow_users_remove": False,
            "allow_users_reset": False,
            "ignores_unused": False,
            "account_id": "-1",
            "calendar_message": "",
            "custom_message": "",
            "display_custom_message": False,
        }

    # ------------------------------------------------------------------
    # Existing tests (preserved from before the CRUD extension)
    # ------------------------------------------------------------------
    def test_invalid_servicepool(self) -> None:
        url = "servicespools/INVALID/overview"

        response = self.client.rest_get(url)
        self.assertEqual(response.status_code, 404)

    def test_service_pools(self) -> None:
        url = "servicespools/overview"

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
            db_pool = models.ServicePool.objects.get(uuid=service_pool["id"])
            self.assertTrue(rest.assertions.assert_servicepool_is(db_pool, service_pool))

    def test_overview_reflects_edit_immediately(self) -> None:
        url = "servicespools/overview"

        pool = models.ServicePool.objects.all()[0]
        self.assertEqual(self.client.rest_get(url).status_code, 200)

        pool.comments = "edited after a first listing"
        pool.save()

        edited = next(p for p in self.client.rest_get(url).json() if p["id"] == pool.uuid)
        self.assertEqual(edited["comments"], pool.comments)

    # ------------------------------------------------------------------
    # CRUD smoke extension (Phase 1 — Safety net)
    # ------------------------------------------------------------------
    def test_get_nonexistent_item_returns_404(self) -> None:
        """GET /servicespools/<nonexistent-uuid> returns 404."""
        response = self.client.rest_get("servicespools/00000000-0000-0000-0000-000000000000")
        self.assertEqual(response.status_code, 404)

    def test_put_creates_new_service_pool(self) -> None:
        """PUT /servicespools (no ID) creates a new ServicePool."""
        before = self._pool_count_in_db()
        payload = self._create_pool_payload(name="smoke-create")
        response = self.client.rest_put("servicespools", data=payload)
        self.assertEqual(response.status_code, 200, response.content)
        item: dict[str, typing.Any] = response.json()
        self.assertIn("id", item)
        self.assertEqual(item["name"], "smoke-create")

        after = self._pool_count_in_db()
        self.assertEqual(after, before + 1, "PUT create must add exactly one ServicePool")
        self.assertTrue(models.ServicePool.objects.filter(uuid=item["id"]).exists())

    def test_put_updates_existing_service_pool(self) -> None:
        """PUT /servicespools/<uuid> updates an existing ServicePool."""
        create_resp = self.client.rest_put("servicespools", data=self._create_pool_payload(name="before"))
        self.assertEqual(create_resp.status_code, 200, create_resp.content)
        new_uuid: str = create_resp.json()["id"]

        update_payload = self._create_pool_payload(name="after")
        update_payload["comments"] = "cambiado"
        update_resp = self.client.rest_put(f"servicespools/{new_uuid}", data=update_payload)
        self.assertEqual(update_resp.status_code, 200, update_resp.content)
        updated: dict[str, typing.Any] = update_resp.json()
        self.assertEqual(updated["id"], new_uuid)
        self.assertEqual(updated["name"], "after")

        db_pool = models.ServicePool.objects.get(uuid=new_uuid)
        self.assertEqual(db_pool.name, "after")
        self.assertEqual(db_pool.comments, "cambiado")

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

        create_resp = self.client.rest_put("servicespools", data=self._create_pool_payload(name="to-delete"))
        new_uuid: str = create_resp.json()["id"]

        visible_before = self._active_pool_count_in_db()
        total_before = self._pool_count_in_db()

        delete_resp = self.client.rest_delete(f"servicespools/{new_uuid}")
        self.assertEqual(delete_resp.status_code, 200, delete_resp.content)
        self.assertEqual(delete_resp.json(), "ok")

        # Visible (non-REMOVABLE) count must drop by exactly 1
        self.assertEqual(
            self._active_pool_count_in_db(),
            visible_before - 1,
            "DELETE on ServicePool must reduce the active (non-REMOVABLE) count by 1",
        )

        # The row may still exist in the DB, but in REMOVABLE state
        db_pool = models.ServicePool.objects.filter(uuid=new_uuid).first()
        self.assertIsNotNone(
            db_pool,
            "Soft-delete semantics: row may remain in DB, marked REMOVABLE",
        )
        assert db_pool is not None  # for type checkers
        self.assertEqual(
            db_pool.state,
            State.REMOVABLE,
            "Soft-delete semantics: state must be REMOVABLE after DELETE",
        )
        # Total row count is unchanged
        self.assertEqual(
            self._pool_count_in_db(),
            total_before,
            "Soft-delete semantics: total row count must NOT change after DELETE",
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

        create_resp = self.client.rest_put("servicespools", data=self._create_pool_payload(name="to-delete-3"))
        new_uuid: str = create_resp.json()["id"]

        self.client.rest_delete(f"servicespools/{new_uuid}")

        # GET of the soft-deleted pool returns 200, not 404 (because row is in DB)
        response = self.client.rest_get(f"servicespools/{new_uuid}")
        self.assertEqual(response.status_code, 200, response.content)
        item: dict[str, typing.Any] = response.json()
        self.assertEqual(item["id"], new_uuid)
        self.assertEqual(
            item["state"],
            State.REMOVABLE,
            "After DELETE, GET must report state=REMOVABLE (soft-delete contract).",
        )

    # ------------------------------------------------------------------
    # fallback_access custom method (GET reads, POST writes)
    # ------------------------------------------------------------------
    # These tests pin the *current* REST contract after the
    # ``fallbackAccess`` -> ``fallback_access`` migration on services_pools:
    #
    # * The custom-method URL is ``/servicespools/<id>/fallback_access`` for
    #   both verbs. GET reads, POST writes.
    # * The POST body uses the snake_case key ``fallback_access``.
    # * For backwards compatibility, the POST body also accepts the legacy
    #   ``fallback`` key (handled inside ``fallback_access``).
    # * The custom method returns the final value as a plain string
    #   (``"ALLOW"`` or ``"DENY"``).

    def test_get_fallback_access_default_is_allow(self) -> None:
        """GET /servicespools/<id>/fallback_access on a fresh pool returns ALLOW.

        ``models.ServicePool.fallbackAccess`` defaults to ``State.ALLOW``.
        """
        pool = self._create_pool_for_fallback_tests()

        response = self.client.rest_get(f"servicespools/{pool.uuid}/fallback_access")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), State.ALLOW)

    def test_post_fallback_access_snake_case(self) -> None:
        """POST /servicespools/<id>/fallback_access with snake_case body key.

        Mirrors what the admin GUI sends (see gui/admin src/app/types/rest.ts
        and meta-pools-detail.component.ts after the
        ``fallbackAccess`` -> ``fallback_access`` migration).
        """
        pool = self._create_pool_for_fallback_tests()

        response = self.client.rest_post(
            f"servicespools/{pool.uuid}/fallback_access",
            data={"fallback_access": State.DENY},
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), State.DENY)

        # Persisted on the model (DB row), not just echoed in the response.
        pool.refresh_from_db()
        self.assertEqual(pool.fallbackAccess, State.DENY)

        # And GET fallback_access now reports the new value.
        get_resp = self.client.rest_get(f"servicespools/{pool.uuid}/fallback_access")
        self.assertEqual(get_resp.status_code, 200, get_resp.content)
        self.assertEqual(get_resp.json(), State.DENY)

    def test_post_fallback_access_legacy_keyword_still_works(self) -> None:
        """POST with the legacy ``fallback`` key keeps working (transitional).

        ``fallback_access`` looks up ``self._params['fallback_access']``
        first and then ``self.params['fallback']`` as a fallback. Pin that
        behaviour so the legacy client is not silently broken.
        """
        pool = self._create_pool_for_fallback_tests()

        response = self.client.rest_post(
            f"servicespools/{pool.uuid}/fallback_access",
            data={"fallback": State.DENY},
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), State.DENY)

        pool.refresh_from_db()
        self.assertEqual(pool.fallbackAccess, State.DENY)

    def test_post_fallback_access_is_idempotent(self) -> None:
        """Calling POST fallback_access repeatedly with the same value is a no-op.

        Five POSTs with the same target value must end up with exactly one
        logical row in the DB (pool count unchanged) and the same final value.
        """
        pool = self._create_pool_for_fallback_tests()
        pool_count_before = models.ServicePool.objects.count()

        for _ in range(5):
            resp = self.client.rest_post(
                f"servicespools/{pool.uuid}/fallback_access",
                data={"fallback_access": State.DENY},
            )
            self.assertEqual(resp.status_code, 200, resp.content)
            self.assertEqual(resp.json(), State.DENY)

        # No extra ServicePool rows were created.
        self.assertEqual(models.ServicePool.objects.count(), pool_count_before)

        pool.refresh_from_db()
        self.assertEqual(pool.fallbackAccess, State.DENY)

    def test_post_fallback_access_round_trip_allow_to_deny(self) -> None:
        """Round-trip DENY -> ALLOW leaves the pool in ALLOW state."""
        pool = self._create_pool_for_fallback_tests()

        # First deny.
        deny = self.client.rest_post(
            f"servicespools/{pool.uuid}/fallback_access",
            data={"fallback_access": State.DENY},
        )
        self.assertEqual(deny.status_code, 200, deny.content)
        self.assertEqual(deny.json(), State.DENY)

        # Then allow.
        allow = self.client.rest_post(
            f"servicespools/{pool.uuid}/fallback_access",
            data={"fallback_access": State.ALLOW},
        )
        self.assertEqual(allow.status_code, 200, allow.content)
        self.assertEqual(allow.json(), State.ALLOW)

        pool.refresh_from_db()
        self.assertEqual(pool.fallbackAccess, State.ALLOW)

    # --- forecast custom method ---

    def _create_forecast_data(self, pool: models.ServicePool, hours: int = 24) -> None:
        """Seed StatsCountersAccum HOUR rows for the pool so forecast has data."""
        now = timezone.now()
        records = [
            models.StatsCountersAccum(
                owner_type=types.stats.CounterOwnerType.SERVICEPOOL,
                owner_id=pool.id,
                counter_type=types.stats.CounterType.INUSE,
                interval_type=models.StatsCountersAccum.IntervalType.HOUR,
                stamp=int((now - datetime.timedelta(hours=hours - i)).replace(minute=0, second=0).timestamp()),
                v_count=6,
                v_sum=6 * 5,
                v_max=5,
                v_min=0,
            )
            for i in range(hours)
        ]
        models.StatsCountersAccum.objects.bulk_create(records)

    def test_get_forecast_empty_pool(self) -> None:
        """GET forecast on a pool with no stats returns has_data=False."""
        Cache.delete(pred_consts.PROFILE_CACHE_OWNER)
        pool = self._create_pool_for_fallback_tests()

        response = self.client.rest_get(f"servicespools/{pool.uuid}/forecast")

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["has_data"], False)
        self.assertEqual(body["samples"], 0)
        self.assertEqual(body["counter"], "inuse")
        self.assertEqual(len(body["points"]), 72)  # default hours

    def test_get_forecast_with_data(self) -> None:
        """GET forecast on a pool with stats returns predicted points."""
        Cache.delete(pred_consts.PROFILE_CACHE_OWNER)
        pool = self._create_pool_for_fallback_tests()
        self._create_forecast_data(pool, hours=24)

        response = self.client.rest_get(f"servicespools/{pool.uuid}/forecast")

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["has_data"], True)
        self.assertGreater(body["samples"], 0)
        self.assertEqual(len(body["points"]), 72)
        # Points should have the expected keys
        point = body["points"][0]
        self.assertIn("stamp", point)
        self.assertIn("p50", point)
        self.assertIn("p75", point)
        self.assertIn("p90", point)
        self.assertIn("max", point)
        self.assertIn("has_data", point)

    def test_get_forecast_custom_hours(self) -> None:
        """GET forecast with ?hours=24 returns 24 points."""
        Cache.delete(pred_consts.PROFILE_CACHE_OWNER)
        pool = self._create_pool_for_fallback_tests()
        self._create_forecast_data(pool, hours=24)

        response = self.client.rest_get(f"servicespools/{pool.uuid}/forecast?hours=24")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.json()["points"]), 24)

    def test_get_forecast_counter_param(self) -> None:
        """GET forecast with ?counter=cached uses CACHED counter."""
        Cache.delete(pred_consts.PROFILE_CACHE_OWNER)
        pool = self._create_pool_for_fallback_tests()
        self._create_forecast_data(pool, hours=24)

        response = self.client.rest_get(f"servicespools/{pool.uuid}/forecast?counter=cached")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["counter"], "cached")

    # --- cache_recommendations custom method ---

    def test_get_cache_recommendations_empty_pool(self) -> None:
        """GET cache_recommendations on a pool with no stats returns all NO_DATA."""
        Cache.delete(pred_consts.PROFILE_CACHE_OWNER)
        pool = self._create_pool_for_fallback_tests()

        response = self.client.rest_get(f"servicespools/{pool.uuid}/cache_recommendations")

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["has_data"], False)
        self.assertEqual(len(body["slots"]), 24)
        self.assertTrue(all(s["verdict"] == "NO_DATA" for s in body["slots"]))
        self.assertIn("current_config", body)
        self.assertEqual(body["summary"]["no_data_hours"], 24)

    def test_get_cache_recommendations_with_data(self) -> None:
        """GET cache_recommendations on a pool with stats returns verdicts."""
        Cache.delete(pred_consts.PROFILE_CACHE_OWNER)
        pool = self._create_pool_for_fallback_tests()
        self._create_forecast_data(pool, hours=24)

        response = self.client.rest_get(f"servicespools/{pool.uuid}/cache_recommendations")

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["has_data"], True)
        self.assertEqual(len(body["slots"]), 24)
        self.assertIn("summary", body)
        summary = body["summary"]
        self.assertEqual(
            summary["starved_hours"]
            + summary["excess_hours"]
            + summary["saturated_hours"]
            + summary["ok_hours"]
            + summary["no_data_hours"],
            24,
        )
