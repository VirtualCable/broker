"""``servicepool.cached.delete``: purging one cached user service.

Only ``delete`` exists (nothing can be created in the cache on demand,
cached rows have no proposable fields). ``delete`` purges one cached
service through the DELETE on the ``cache`` detail item. ``read`` mirrors
the cached collection plus the capability flags. FORCE like the rest of
the pool operations (the cleaners walk the removal states asynchronously;
REST holds the authoritative checks at execution time). Purging is gated
by the pool's ``uses_cache`` capability.
"""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.util.model import sql_now
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.base import ActionOperation, MutableActionType, StalePolicy
from uds.mutability.types.service_pools.cached import ServicePoolCached
from uds.REST.methods.services_pools import ServicesPools
from uds.REST.methods.user_services import CachedService

from tests.mcp.mutability._helpers import FlowTestCase, make_request

State = types.states.State
CacheLevel = types.services.CacheLevel


def _pool(cache: bool = True) -> models.ServicePool:
    from tests.fixtures.services import (
        create_db_osmanager,
        create_db_provider,
        create_db_service,
        create_db_servicepool,
    )

    service = create_db_service(create_db_provider(), use_caching_version=cache)
    return create_db_servicepool(service=service, osmanager=create_db_osmanager())


def _publication(pool: models.ServicePool) -> models.ServicePoolPublication:
    now = sql_now()
    return models.ServicePoolPublication.objects.create(
        deployed_service=pool,
        publish_date=now,
        state_date=now,
        state=State.USABLE,
        revision=pool.current_pub_revision,
    )


def _cached(
    pool: models.ServicePool,
    state: str = State.USABLE,
    cache_level: int = CacheLevel.L1,
) -> models.UserService:
    from tests.fixtures.services import create_db_userservice

    row = create_db_userservice(pool, _publication(pool), user=None)
    row.cache_level = cache_level
    row.state = state
    row.save(update_fields=["cache_level", "state"])
    return row


def _action_type() -> MutableActionType:
    factory = registry_get("servicepool.cached.delete")
    assert factory is not None
    return factory()


class ServicePoolCachedRegistryTest(FlowTestCase):
    def test_only_delete_is_registered(self) -> None:
        self.assertIs(type(_action_type()), ServicePoolCached)
        self.assertIn("servicepool.cached.delete", all_type_ids())
        self.assertNotIn("servicepool.cached.add", all_type_ids())
        self.assertEqual(ServicePoolCached.supported_operations(), frozenset({ActionOperation.DELETE}))

    def test_read_override_publishes_a_read(self) -> None:
        self.assertTrue(ServicePoolCached.readable())

    def test_stale_policy_is_force(self) -> None:
        self.assertIs(_action_type().get_stale_policy(), StalePolicy.FORCE)

    def test_is_target_scoped(self) -> None:
        self.assertIs(ServicePoolCached.target_scoped_fields, True)

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            _action_type().resolve_target("00000000-0000-0000-0000-000000000000")

    def test_read_view_publishes_cached_services_and_capabilities(self) -> None:
        pool = _pool()
        row = _cached(pool, cache_level=CacheLevel.L2)
        view = ServicePoolCached().read(pool.uuid)
        self.assertEqual(view["entity_type"], "servicepool.cached")
        self.assertEqual(view["target_uuid"], pool.uuid)
        self.assertIn("uses_cache", view["capabilities"])
        by_uuid = {item["uuid"]: item for item in view["cached_user_services"]}
        self.assertEqual(by_uuid[row.uuid]["state"], State.USABLE)
        self.assertEqual(by_uuid[row.uuid]["cache_level"], CacheLevel.L2)


class ServicePoolCachedFieldsTest(FlowTestCase):
    def test_publishes_only_the_user_service_field(self) -> None:
        defs = _action_type().field_definitions("servicepool")
        self.assertEqual([d["name"] for d in defs], ["user_service"])
        self.assertEqual(defs[0]["type"], types.ui.FieldType.TEXT.value)

    def test_tooltip_lists_valid_members(self) -> None:
        pool = _pool()
        row = _cached(pool)
        defs = _action_type().field_definitions("servicepool", pool)
        self.assertIn(row.uuid, defs[0]["tooltip"])

    def test_tooltip_reports_no_cache_support(self) -> None:
        pool = _pool(cache=False)
        defs = _action_type().field_definitions("servicepool", pool)
        self.assertIn("does not use caching", defs[0]["tooltip"])

    def test_tooltip_reports_nothing_purgeable(self) -> None:
        pool = _pool()
        defs = _action_type().field_definitions("servicepool", pool)
        self.assertIn("no cached user service in a valid state", defs[0]["tooltip"])


class ServicePoolCachedValidationTest(FlowTestCase):
    def test_user_service_field_is_required(self) -> None:
        pool = _pool()
        errors = _action_type().validate_values("servicepool", {}, pool)
        self.assertTrue(any("user_service is required" in e for e in errors))

    def test_purge_requires_the_uses_cache_capability(self) -> None:
        pool = _pool(cache=False)
        errors = _action_type().validate_values("servicepool", {"user_service": "0" * 32}, pool)
        self.assertTrue(any("does not use caching" in e for e in errors))

    def test_valid_purge_when_supported(self) -> None:
        pool = _pool()
        row = _cached(pool)
        self.assertEqual(_action_type().validate_values("servicepool", {"user_service": row.uuid}, pool), [])

    def test_must_be_a_cached_service_of_the_pool(self) -> None:
        pool = _pool()
        other = _cached(_pool())
        errors = _action_type().validate_values("servicepool", {"user_service": other.uuid}, pool)
        self.assertTrue(any("is not a cached service" in e for e in errors))

    def test_only_purgeable_states_are_accepted(self) -> None:
        pool = _pool()
        removed = _cached(pool, state=State.REMOVED)
        errors = _action_type().validate_values("servicepool", {"user_service": removed.uuid}, pool)
        self.assertTrue(any("can be purged" in e for e in errors))
        for state in (State.USABLE, State.PREPARING, State.REMOVING):
            row = _cached(pool, state=state)
            self.assertEqual(
                _action_type().validate_values("servicepool", {"user_service": row.uuid}, pool), []
            )

    def test_assigned_service_is_not_cached(self) -> None:
        pool = _pool()
        from tests.fixtures.services import create_db_userservice

        assigned = create_db_userservice(pool, _publication(pool), user=self.owner)
        errors = _action_type().validate_values("servicepool", {"user_service": assigned.uuid}, pool)
        self.assertTrue(any("is not a cached service" in e for e in errors))

    def test_malformed_uuid_rejected(self) -> None:
        pool = _pool()
        errors = _action_type().validate_values("servicepool", {"user_service": "not-a-uuid"}, pool)
        self.assertTrue(any("not a valid uuid" in e for e in errors))
        errors = _action_type().validate_values("servicepool", {"user_service": 7}, pool)
        self.assertTrue(any("must be the uuid string" in e for e in errors))


class ServicePoolCachedFingerprintTest(FlowTestCase):
    def test_fingerprint_tracks_purgeable_cached_rows(self) -> None:
        pool = _pool()
        action_type = _action_type()
        self.assertEqual(
            action_type.snapshot_values(pool, ["cached_user_services"]), {"cached_user_services": []}
        )
        base = action_type.fingerprint(pool)
        self.assertEqual(base, action_type.fingerprint(pool))

        row = _cached(pool)
        self.assertNotEqual(base, action_type.fingerprint(pool))
        snapshot = action_type.snapshot_values(pool, ["cached_user_services"])["cached_user_services"]
        self.assertEqual([entry["uuid"] for entry in snapshot], [row.uuid])
        # A removed row leaves the purgeable set; fingerprint is back to base
        row.state = State.REMOVED
        row.save(update_fields=["state"])
        self.assertEqual(base, action_type.fingerprint(pool))


class ServicePoolCachedExecuteTest(FlowTestCase):
    def _run(self, pool: models.ServicePool, values: dict[str, typing.Any]) -> mock.MagicMock:
        action = self._action(
            self._flow(),
            action_type="servicepool.cached.delete",
            target_uuid=pool.uuid,
            values=values,
            base_values={},
            base_etag="etag",
        )
        proxy_cls = mock.MagicMock()
        proxy_cls.return_value.execute = mock.AsyncMock(return_value=None)
        with mock.patch("uds.mutability.types.service_pools.cached.RestProxy", proxy_cls):
            summary = async_to_sync(_action_type().execute)(action, request=make_request())
        self._last_summary = summary
        return proxy_cls

    def test_purge_deletes_the_cache_item(self) -> None:
        pool = _pool()
        row = _cached(pool)
        proxy_cls = self._run(pool, {"user_service": row.uuid})
        call = proxy_cls.return_value.execute.await_args_list[0]
        target = call[0][0]
        self.assertIs(target.handler, CachedService)
        self.assertEqual(target.path, "services_pools/{uuid}/cache")
        self.assertEqual(target.method, types.rest.CustomMethodMethod.DELETE)
        self.assertEqual(target.args, (row.uuid,))
        self.assertIs(target.parent.handler, ServicesPools)
        self.assertEqual(call[1]["parent_uuid"], pool.uuid)
        self.assertIn("purging", self._last_summary)

    def test_purge_aborts_when_the_cache_is_unsupported(self) -> None:
        pool = _pool()
        row = _cached(pool)
        uuid_value = row.uuid
        pool.service.provider  # noqa: B018  (keep the pool alive/reloaded)
        with (
            mock.patch(
                "uds.models.service_pool.ServicePool.capabilities",
                return_value=types.services.PoolCapabilities(
                    uses_cache=False, uses_cache_l2=False, can_reset=False, needs_publication=False
                ),
            ),
            self.assertRaises(rest_exceptions.NotSupportedError),
        ):
            self._run(pool, {"user_service": uuid_value})

    def test_purge_aborts_when_the_row_vanished(self) -> None:
        pool = _pool()
        row = _cached(pool)
        uuid_value = row.uuid
        row.delete()
        with self.assertRaises(rest_exceptions.RequestError):
            self._run(pool, {"user_service": uuid_value})

    def test_purge_aborts_when_state_advanced(self) -> None:
        pool = _pool()
        row = _cached(pool)
        row.state = State.REMOVED
        row.save(update_fields=["state"])
        with self.assertRaises(rest_exceptions.RequestError):
            self._run(pool, {"user_service": row.uuid})
