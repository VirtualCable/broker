"""``servicepool.publication``: publish a service pool or cancel a running publication.

Publications are *operations*, never fields: a pool publishes with the
canonical ``services_pools/{uuid}/publications`` custom methods, so this
family exposes exactly the two write verbs the REST surface provides:

``add``     — initiate a publication (optional changelog message). The
              server rejects it when another publication is still running
              or the pool is in maintenance; the same checks run here at
              propose time so a doomed proposal fails early.
``delete``  — cancel a running publication by its uuid (identified by its
              revision in the read view). The REST cancel is the same
              double-invocation force-cancel the admin UI uses.

The ``read`` view mirrors the two read-only detail collections of the
pool: every publication row (uuid, revision, dates, state) and the whole
changelog (revision, stamp, log). Both are plain GETs — immutable lists,
never proposable — and changelog entries are joined to publications by
``revision``, the natural key the pool uses for them.

Both operations run under FORCE: a publication advances asynchronously
(the whole reason the fingerprint exists), so a state change while the
proposal is pending never invalidates it; execution re-reads the live
state and the REST layer holds the authoritative checks.
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
from uds.REST.methods.user_services import Publications

from ... import base as mutability_base
from ...base import ActionOperation, StalePolicy

JsonObject = dict[str, typing.Any]

State = types.states.State

_CANCELABLE_STATES: typing.Final[frozenset[str]] = frozenset([*State.PUBLISH_STATES, State.CANCELING])
_INTERESTING_STATES: typing.Final[frozenset[str]] = frozenset(
    [*State.PUBLISH_STATES, State.CANCELING, State.USABLE]
)

PublicationRow = dict[str, typing.Any]


def _publication_rows(pool: models.ServicePool, limit: int | None = None) -> list[PublicationRow]:
    """Live publication rows of the pool, newest revision first."""
    rows = pool.publications.order_by("-revision", "id")
    if limit is not None:
        rows = rows[:limit]
    return [
        {
            "uuid": row.uuid,
            "revision": row.revision,
            "publish_date": row.publish_date.isoformat() if row.publish_date else None,
            "state": row.state,
            "state_label": State.from_str(row.state).localized,
            "state_date": row.state_date.isoformat() if row.state_date else None,
        }
        for row in rows
    ]


def _cancelable_rows(pool: models.ServicePool) -> list[PublicationRow]:
    return [row for row in _publication_rows(pool) if row["state"] in _CANCELABLE_STATES]


def _cas_state(pool: models.ServicePool) -> JsonObject:
    """Canonical CAS payload of the publication surface."""
    interesting = [
        {"uuid": row["uuid"], "state": row["state"]}
        for row in _publication_rows(pool)
        if row["state"] in _INTERESTING_STATES
    ]
    return {"revision": pool.current_pub_revision, "in_progress": interesting}


class ServicePoolPublications(mutability_base.MutableActionType):
    """Proposal: publish a service pool or cancel one of its running publications."""

    type_id = "servicepool.publication"

    handler = ServicesPools
    model = models.ServicePool
    noun = "Service pool"
    # A publication is async: pending proposals stay valid while it
    # advances, execution re-reads and REST holds the authoritative checks.
    stale_policy = StalePolicy.FORCE
    # Discovery without the pool is useless: the cancelable publications
    # and the next revision depend on the concrete pool.
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

    def _op(self) -> ActionOperation:
        """The operation this instance is bound to (the family has two)."""
        if self.operation is None:
            raise ValueError(f"{self.type_id}: publication types must be bound to an operation")
        return self.operation

    # ---------------------------------------------------------- discovery

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        operation = self.operation
        definitions: dict[str, JsonObject] = {
            "changelog": {
                "name": "changelog",
                "type": types.ui.FieldType.TEXT.value,
                "label": "Changelog message",
                "tooltip": (
                    "Optional message describing this publication, stored in the pool changelog "
                    "under the published revision."
                ),
                "secret": False,
            },
            "publication": {
                "name": "publication",
                "type": types.ui.FieldType.TEXT.value,
                "label": "Publication uuid",
                "tooltip": self._cancel_tooltip(target),
                "secret": False,
            },
        }
        if operation is ActionOperation.ADD:
            return [definitions["changelog"]]
        if operation is ActionOperation.DELETE:
            return [definitions["publication"]]
        return list(definitions.values())

    def _cancel_tooltip(self, target: db_models.Model | None) -> str:
        base = (
            "uuid of the running publication to cancel (only publications that are "
            "launching, preparing or canceling can be canceled). "
        )
        if isinstance(target, models.ServicePool):
            if not target.capabilities().needs_publication:
                return (
                    base
                    + "This pool does not support publications: publishing and canceling are not available."
                )
            rows = _cancelable_rows(target)
            if rows:
                listed = ", ".join(f"revision {row['revision']} = {row['uuid']}" for row in rows)
                return base + f"Currently cancelable: {listed}."
            return base + "This pool has no cancelable publication right now."
        return base + "Discover the uuids with get_servicepool_publication."

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
        operation = self._op()
        if target is not None and not typing.cast(models.ServicePool, target).capabilities().needs_publication:
            return [*errors, "this service pool does not support publications (its service type needs none)"]
        if operation is ActionOperation.ADD:
            changelog = flat.get("changelog")
            if changelog is not None and not isinstance(changelog, str):
                errors.append("field changelog must be a string (or omitted)")
            if target is not None:
                pool = typing.cast(models.ServicePool, target)
                if pool.publications.filter(state__in=list(State.PUBLISH_STATES)).count() > 0:
                    errors.append(
                        "this pool is already publishing; wait for the current publication "
                        "to finish (or propose canceling it) before publishing again"
                    )
                if pool.is_in_maintenance():
                    errors.append("this pool is in maintenance mode and new publications are not allowed")
            return errors
        if "publication" not in flat:
            errors.append("field publication is required (uuid of a running publication of this pool)")
            return errors
        errors.extend(self._validate_cancelable(flat["publication"], target))
        return errors

    def _validate_cancelable(self, value: typing.Any, target: db_models.Model | None) -> list[str]:
        if not isinstance(value, str):
            return ["field publication must be the uuid string of a running publication of this pool"]
        try:
            pub_uuid = process_uuid(value)
        except ValueError:
            return [f"field publication is not a valid uuid: {value!r}"]
        if target is None:
            return []
        pool = typing.cast(models.ServicePool, target)
        row = models.ServicePoolPublication.objects.filter(uuid=pub_uuid, deployed_service=pool).first()
        if row is None:
            return [f"field publication {pub_uuid} is not a publication of this service pool"]
        if row.state not in _CANCELABLE_STATES:
            return [
                (
                    f"publication {pub_uuid} (revision {row.revision}) is not running "
                    f"(state {row.state}); only launching, preparing or canceling publications can be canceled"
                )
            ]
        return []

    # ------------------------------------------------------------- CAS

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        return ["revision", "in_progress"]

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
            "current_revision": pool.current_pub_revision,
            "publications": _publication_rows(pool),
            "changelog": [
                {
                    "revision": entry.revision,
                    "stamp": entry.stamp.isoformat() if entry.stamp else None,
                    "log": entry.log,
                }
                for entry in pool.changelog.order_by("-revision", "id")
            ],
        }

    @typing.override
    def read_title(self) -> str:
        return "Read service pool publications"

    @typing.override
    def read_description(self) -> str:
        return (
            "Read the publications and the changelog of one service pool: every "
            "publication with its uuid (to cancel a running one), revision, dates and "
            "state, plus the changelog entries joined by revision. Both lists are "
            "read-only history; publishing and canceling go through the add/delete "
            "proposal tools. Applies immediately; it is a plain read, never a proposal."
        )

    # -------------------------------------------------------- tool text

    @typing.override
    def tool_title(self) -> str:
        if self._op() is ActionOperation.ADD:
            return "Propose publishing a service pool"
        return "Propose canceling a service pool publication"

    @typing.override
    def tool_description(self) -> str:
        pool = "service pool"
        if self._op() is ActionOperation.ADD:
            return (
                f"Propose to publish a {pool} (start a new publication of its current "
                "configuration). The optional 'changelog' field stores a message under "
                "the published revision. It is rejected when another publication is "
                "still running or the pool is in maintenance. Read the current state "
                "with get_servicepool_publication. The proposal does NOT apply "
                "anything: it is queued until an administrator approves it."
            )
        return (
            f"Propose to cancel a running publication of a {pool}. The 'publication' "
            "field is the uuid of a launching/preparing/canceling publication (see "
            "get_servicepool_publication). Canceling a publication that is already "
            "canceling forces it (unclean: resources may need manual review), exactly "
            "like the admin UI double-invocation. The proposal does NOT apply "
            "anything: it is queued until an administrator approves it."
        )

    # ---------------------------------------------------------- execution

    @typing.override
    async def op_add(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        def _plan() -> tuple[str, JsonObject]:
            pool = typing.cast(models.ServicePool, self.resolve_target(action.target_uuid))
            running = pool.publications.filter(state__in=list(State.PUBLISH_STATES)).count()
            if running > 0:
                raise rest_exceptions.RequestError(
                    f'Service pool "{pool.name}" is already publishing; '
                    "wait for the current publication to finish (or cancel it) and propose again"
                )
            if pool.is_in_maintenance():
                raise rest_exceptions.RequestError(
                    f'Service pool "{pool.name}" is in maintenance mode and new publications are not allowed'
                )
            params: JsonObject = {}
            changelog = action.values.get("changelog")
            if isinstance(changelog, str) and changelog.strip():
                params["changelog"] = changelog
            return pool.name, params

        pool_name, params = await sync_to_async(_plan, thread_sensitive=True)()
        await self._post(request, action.target_uuid, ("publish",), params)
        return f'Service pool "{pool_name}" publication initiated'

    @typing.override
    async def op_delete(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        def _plan() -> tuple[str, str, int]:
            pool = typing.cast(models.ServicePool, self.resolve_target(action.target_uuid))
            pub_uuid = process_uuid(typing.cast(str, action.values["publication"]))
            row = models.ServicePoolPublication.objects.filter(uuid=pub_uuid, deployed_service=pool).first()
            if row is None:
                raise rest_exceptions.RequestError(
                    f'Service pool "{pool.name}" no longer has publication {pub_uuid} '
                    "(it disappeared after the proposal was approved)"
                )
            if row.state not in _CANCELABLE_STATES:
                raise rest_exceptions.RequestError(
                    f"publication {pub_uuid} (revision {row.revision}) is no longer running "
                    f"(state {row.state}); nothing to cancel"
                )
            return pool.name, pub_uuid, row.revision

        pool_name, pub_uuid, revision = await sync_to_async(_plan, thread_sensitive=True)()
        await self._post(request, action.target_uuid, (pub_uuid, "cancel"), {})
        return f'Service pool "{pool_name}" publication revision {revision} canceling'

    @staticmethod
    async def _post(
        request: ExtendedHttpRequestWithUser,
        pool_uuid: str,
        args: tuple[str, ...],
        params: JsonObject,
    ) -> typing.Any:
        return await RestProxy().execute(
            RestTarget(
                Publications,
                "services_pools/{uuid}/publications",
                types.rest.CustomMethodMethod.POST,
                args=args,
                parent=RestTarget(ServicesPools, "services_pools"),
            ),
            request,
            params,
            parent_uuid=pool_uuid,
        )
