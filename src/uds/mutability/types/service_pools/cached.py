"""``servicepool.cached``: purge one cached user service of a pool.

Operations over the ``cache`` detail collection of a pool, never fields:
``delete`` purges one cached service (hard-ish release of an unassigned
cached row, exactly the DELETE the admin UI issues on
``services_pools/{uuid}/cache/{uuid}``). There is no ``add``: nothing can
be created in the cache on demand — the actors fill it while publishing —
and there is no ``update``: cached rows have no proposable properties.
Members are discovered through the external immutable
``list_services_pools_cache`` list tool; the read view mirrors it plus
the pool's capability flags.

FORCE like the rest of the pool operations: purging walks
USABLE -> REMOVING -> REMOVABLE through the cleaners asynchronously, so
a pending proposal stays valid while the state advances, execution
re-reads the live row and REST holds the authoritative checks. A pool
whose service type does not use caching answers NotSupportedError at
REST (the gate added with the capability contract); the same rule is
checked here at propose time so a doomed proposal fails early.
"""

import collections.abc
import hashlib
import json
import typing

from asgiref.sync import sync_to_async
from django.db import models as db_models

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.core.util.model import process_uuid
from uds.mcp.rest_proxy import RestProxy, RestTarget
from uds.REST.methods.services_pools import ServicesPools
from uds.REST.methods.user_services import CachedService

from ... import base as mutability_base
from ...base import StalePolicy

JsonObject = dict[str, typing.Any]

State = types.states.State

_PURGEABLE_STATES: typing.Final[frozenset[str]] = frozenset([State.USABLE, State.PREPARING, State.REMOVING])
_MAX_TOOLTIP_MEMBERS: typing.Final[int] = 10

CachedServiceRow = dict[str, typing.Any]


def _cached_rows(pool: models.ServicePool, limit: int | None = None) -> list[CachedServiceRow]:
    """Live cached rows of the pool, newest first."""
    rows = pool.cached_users_services().order_by("-creation_date", "-id")
    if limit is not None:
        rows = rows[:limit]
    return [
        {
            "uuid": row.uuid,
            "friendly_name": row.friendly_name,
            "state": row.state,
            "state_label": State.from_str(row.state).localized,
            "cache_level": row.cache_level,
            "in_use": row.in_use,
        }
        for row in rows
    ]


def _cas_state(pool: models.ServicePool) -> JsonObject:
    """Canonical CAS payload of the cache surface."""
    rows = [
        {"uuid": row["uuid"], "state": row["state"]}
        for row in _cached_rows(pool)
        if row["state"] in _PURGEABLE_STATES
    ]
    return {"cached_user_services": rows}


class ServicePoolCached(mutability_base.MutableActionType):
    """Proposal: purge one cached user service of a service pool."""

    type_id = "servicepool.cached"

    handler = ServicesPools
    model = models.ServicePool
    noun = "Service pool"
    # Purging is asynchronous (the cleaners walk the removal states):
    # pending proposals stay valid, execution re-reads, REST decides.
    stale_policy = StalePolicy.FORCE
    # Discovery without the pool is useless: the cached rows and the
    # uses_cache capability belong to the concrete pool.
    target_scoped_fields = True

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.ServicePool.objects.get(uuid__iexact=target_uuid)
        except models.ServicePool.DoesNotExist:
            raise rest_exceptions.NotFound("Service pool not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return "servicepool"

    # ---------------------------------------------------------- discovery

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        return [
            {
                "name": "user_service",
                "type": types.ui.FieldType.TEXT.value,
                "label": "Cached user service uuid",
                "tooltip": self._user_service_tooltip(target),
                "secret": False,
            }
        ]

    def _user_service_tooltip(self, target: db_models.Model | None) -> str:
        base = (
            "uuid of the cached user service to purge (it is released and freed, never "
            "reassigned; usable, preparing or removing services can be purged). "
        )
        if isinstance(target, models.ServicePool):
            if not target.capabilities().uses_cache:
                return base + "This pool's service type does not use caching: purging is not available."
            listed = [
                f"{row['uuid']} ({row['friendly_name']})"
                for row in _cached_rows(target)
                if row["state"] in _PURGEABLE_STATES
            ][:_MAX_TOOLTIP_MEMBERS]
            if listed:
                return base + f"Currently valid: {', '.join(listed)}."
            return base + "This pool has no cached user service in a valid state to purge right now."
        return base + "Discover the uuids with list_services_pools_cache or get_servicepool_cached."

    # -------------------------------------------------------- validation

    @typing.override
    def validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        errors = super().validate_values(for_type, values, target)
        flat = self.flatten_values(values)
        if target is not None and not typing.cast(models.ServicePool, target).capabilities().uses_cache:
            return [
                *errors,
                "this service pool does not use caching (its service type does not), so it has no cache to purge",
            ]
        if "user_service" not in flat:
            errors.append("field user_service is required (uuid of a cached user service of this pool)")
            return errors
        errors.extend(self._validate_member(flat["user_service"], target))
        return errors

    def _validate_member(self, value: typing.Any, target: db_models.Model | None) -> list[str]:
        if not isinstance(value, str):
            return ["field user_service must be the uuid string of a cached user service of this pool"]
        try:
            uuid = process_uuid(value)
        except ValueError:
            return [f"field user_service is not a valid uuid: {value!r}"]
        if target is None:
            return []
        pool = typing.cast(models.ServicePool, target)
        if not pool.capabilities().uses_cache:
            return [
                "this service pool does not use caching (its service type does not), so it has no cache to purge"
            ]
        row = pool.cached_users_services().filter(uuid=uuid).first()
        if row is None:
            return [f"user service {uuid} is not a cached service of this service pool"]
        if row.state not in _PURGEABLE_STATES:
            return [
                (
                    f"user service {uuid} is in state {row.state} ({State.from_str(row.state).localized}); "
                    "only usable, preparing or removing cached services can be purged"
                )
            ]
        return []

    # ------------------------------------------------------------- CAS

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        return ["cached_user_services"]

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        pool = typing.cast(models.ServicePool, target)
        wanted = set(names)
        cas = _cas_state(pool)
        return {name: value for name, value in cas.items() if name in wanted}

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        cas = _cas_state(typing.cast(models.ServicePool, target))
        return hashlib.sha256(json.dumps(cas, sort_keys=True).encode("utf-8")).hexdigest()

    @typing.override
    def target_display_name(self, target: db_models.Model) -> str:
        return typing.cast(models.ServicePool, target).name

    # ------------------------------------------------------------ read

    @typing.override
    def read(self, target_uuid: str) -> JsonObject:
        pool = typing.cast(models.ServicePool, self.resolve_target(target_uuid))
        return {
            "entity_type": self.type_id,
            "target_uuid": self.target_uuid_of(pool),
            "for_type": "servicepool",
            "capabilities": pool.capabilities().as_dict(),
            "cached_user_services": _cached_rows(pool),
        }

    @typing.override
    def read_title(self) -> str:
        return "Read service pool cached user services"

    @typing.override
    def read_description(self) -> str:
        return (
            "Read the cached user services of one service pool with their uuids, "
            "states, cache levels and in-use flags, plus the pool's capability flags "
            "(uses_cache gates the purge operation). Purging goes through the delete "
            "proposal tool. Applies immediately; it is a plain read, never a proposal."
        )

    # -------------------------------------------------------- tool text

    @typing.override
    def tool_title(self) -> str:
        return "Propose purging a cached user service"

    @typing.override
    def tool_description(self) -> str:
        return (
            "Propose to purge (delete) one cached user service of a service pool: the "
            "cached row is released and freed, exactly like the DELETE on the REST cache "
            "detail. Only for pools whose service type uses caching. The 'user_service' "
            "field is the uuid of a cached user service of this pool in usable, "
            "preparing or removing state. Read the current rows with "
            "get_servicepool_cached or list_services_pools_cache. The proposal does NOT "
            "apply anything: it is queued until an administrator approves it."
        )

    # ---------------------------------------------------------- execution

    @typing.override
    async def op_delete(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        def _plan() -> tuple[str, str]:
            pool = typing.cast(models.ServicePool, self.resolve_target(action.target_uuid))
            if not pool.capabilities().uses_cache:
                raise rest_exceptions.NotSupportedError(
                    f'Service pool "{pool.name}" does not use caching, its cache cannot be purged'
                )
            uuid = process_uuid(typing.cast(str, action.values["user_service"]))
            row = pool.cached_users_services().filter(uuid=uuid).first()
            if row is None:
                raise rest_exceptions.RequestError(
                    f'Service pool "{pool.name}" no longer has cached user service {uuid} '
                    "(it disappeared after the proposal was approved)"
                )
            if row.state not in _PURGEABLE_STATES:
                raise rest_exceptions.RequestError(
                    f"cached user service {uuid} is in state {row.state} "
                    f"({State.from_str(row.state).localized}); not purgeable anymore"
                )
            return pool.name, uuid

        pool_name, user_service_uuid = await sync_to_async(_plan, thread_sensitive=True)()
        await RestProxy().execute(
            RestTarget(
                CachedService,
                "services_pools/{uuid}/cache",
                types.rest.CustomMethodMethod.DELETE,
                args=(user_service_uuid,),
                parent=RestTarget(ServicesPools, "services_pools"),
            ),
            request,
            {},
            parent_uuid=action.target_uuid,
        )
        return f'Service pool "{pool_name}" cached user service {user_service_uuid} purging'
