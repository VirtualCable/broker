"""``servicepool.assignable.create``: hand one inventory element of a pool to a user.

Some service types keep a manual inventory of machines they can hand out
(their ``enumerate_assignables``: physical servers, imported VMs, ...).
This family proposes exactly what the administration dialog for those
pools does: the ``create_from_assignable`` custom method on the pool
creates one assigned user service bound to the chosen inventory element
and to one user, and the creation finishes asynchronously (the row
starts in PREPARING; the type's own machinery validates it — or fails
it — afterwards).

The creation is a detail of the pool: the proposal targets the POOL
uuid (``create_needs_parent``), MANAGEMENT over the pool is the create
permission, and there is no subtype gallery (``create_has_gallery``):
the "subtype" is the chosen assignable element, discovered live through
the pool's service (``get_servicepool_assignables`` on the MCP read
side). Propose-time validation mirrors the stable gates the manager
will hit at execution — the pool's service type supports manual
assignment, the user exists — and fails early on the inventory element
being absent from the service's current ``enumerate_assignables``.
Conditions that the live world can legitimately change before approval
(an inventory element taken by the provider's own machinery, a pool
without an active publication yet, the pool's cache limits) are NOT
proposed-time errors: they stay the asynchronous outcome REST reports,
exactly like the FORCE verbs of the sibling assignment family.
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
from uds.mcp.rest_proxy import RestTarget
from uds.REST.methods.services_pools import ServicesPools

from ... import base as mutability_base
from ... import verbs as mutability_verbs

JsonObject = dict[str, typing.Any]

_MAX_TOOLTIP_ASSIGNABLES: typing.Final[int] = 10


def _pool_or_fail(uuid: str) -> models.ServicePool:
    try:
        return models.ServicePool.objects.get(uuid__iexact=uuid)
    except models.ServicePool.DoesNotExist:
        raise rest_exceptions.NotFound("Service pool not found") from None


class ServicePoolAssignable(mutability_base.MutableActionType):
    """Proposal: assign one inventory element of a pool to one user."""

    type_id = "servicepool.assignable"
    handler = ServicesPools
    model = models.ServicePool
    noun = "Inventory assignment"
    # The proposal targets the pool (create_from_assignable is a master
    # custom method of it), and its "subtype" is the inventory element,
    # not a module gallery: no data_type travels.
    create_needs_parent = True
    create_has_gallery = False

    # ------------------------------------------------- generic CAS layer
    # Creations carry no CAS base, but the abstract contract still needs
    # the whole-item view of the pool the proposal points at: the pool's
    # assigned collection is the interesting state around this family.

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        return _pool_or_fail(target_uuid)

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return "servicepool"

    @typing.override
    def target_display_name(self, target: db_models.Model) -> str:
        return typing.cast(models.ServicePool, target).name

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        # This family creates only; the non-create discovery surface is
        # the creation form itself.
        return self.create_field_definitions(for_type, target)

    @typing.override
    def create_field_definitions(
        self, for_type: str, target: db_models.Model | None = None
    ) -> list[JsonObject]:
        tooltip = (
            "id of one inventory element of the pool's service to hand out. "
            "Discover the current ids with get_servicepool_assignables."
        )
        if isinstance(target, models.ServicePool) and target.service.get_type().can_assign():
            inventory = self._live_inventory(target)
            if inventory:
                listed = inventory[:_MAX_TOOLTIP_ASSIGNABLES]
                tooltip = (
                    "id of one inventory element of the pool's service to hand out "
                    f"(currently: {', '.join(listed)})."
                )
            else:
                tooltip = (
                    "id of one inventory element of the pool's service to hand out. "
                    "This pool's service currently reports no assignable inventory."
                )
        return [
            {
                "name": "user_id",
                "type": types.ui.FieldType.TEXT.value,
                "label": "User uuid",
                "tooltip": (
                    "uuid of the User that will receive the assigned service. "
                    "Discover user uuids with list_authenticators_users."
                ),
                "secret": False,
            },
            {
                # Deliberately not a fixed CHOICE: the inventory is live
                # provider state, and a proposal whose element was taken
                # meanwhile must fail at execution with a fresh error,
                # not be pinned to propose-time choices. The tooltip
                # lists the current ids as a convenience.
                "name": "assignable_id",
                "type": types.ui.FieldType.TEXT.value,
                "label": "Assignable id",
                "tooltip": tooltip,
                "secret": False,
            },
        ]

    # -------------------------------------------------------- validation

    @typing.override
    def create_validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        # The family has no subtype gallery: the data_type is not required.
        errors = super().create_validate_values(for_type or "servicepool", values, target)
        user = self._validate_user(values.get("user_id"))
        if isinstance(user, str):
            errors.append(user)
        if not str(values.get("assignable_id", "")).strip():
            errors.append("field assignable_id: required (one inventory element of the pool)")
        if not isinstance(target, models.ServicePool):
            return errors
        if not target.service.get_type().can_assign():
            errors.append("this pool's service type does not support assigning inventory elements manually")
            return errors
        assignable_id = str(values.get("assignable_id", "")).strip()
        if assignable_id:
            inventory = self._live_inventory(target)
            if assignable_id not in inventory:
                listed = (
                    ", ".join(inventory)
                    if len(inventory) <= _MAX_TOOLTIP_ASSIGNABLES
                    else f"{len(inventory)} elements"
                )
                errors.append(f"assignable '{assignable_id}' is not in the pool's inventory ({listed})")
        # The manager creates from the pool's active publication for
        # types that have publications, and raises without one: the
        # condition depends on the service type definition, not on the
        # live world, so it belongs to propose-time validation.
        if target.service.get_type().publication_type is not None and target.active_publication() is None:
            errors.append("this pool has no active publication to create the assigned service from")
        return errors

    @staticmethod
    def _live_inventory(pool: models.ServicePool) -> list[str]:
        """Current assignable ids of the pool's service.

        The provider call can fail (transport down): an empty inventory
        reports "not in the inventory", which is honest for a proposal
        that would fail the same way at execution; it never raises out
        of validation.
        """
        try:
            return [str(item.id) for item in pool.service.get_instance().enumerate_assignables()]
        except Exception:
            return []

    @staticmethod
    def _validate_user(value: typing.Any) -> "models.User | str":
        """The receiving user, or an error message."""
        if not isinstance(value, str):
            return "field user_id must be the uuid string of a user"
        try:
            uuid = process_uuid(value)
        except ValueError:
            return f"field user_id is not a valid uuid: {value!r}"
        user = models.User.objects.filter(uuid=uuid).first()
        if user is None:
            return f"user {value} does not exist"
        return user

    # ------------------------------------------------------------ CAS

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        if "user_services" not in set(names):
            return {}
        pool = typing.cast(models.ServicePool, target)
        return {
            "user_services": [
                {"uuid": row.uuid, "user": row.user.uuid if row.user else None}
                for row in pool.assigned_user_services().order_by("uuid")
            ]
        }

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        return ["user_services"]

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        cas = self.snapshot_values(target, self.etag_fields("servicepool"))
        return hashlib.sha256(json.dumps(cas, sort_keys=True).encode("utf-8")).hexdigest()

    # ---------------------------------------------------- standard texts

    @typing.override
    def tool_title(self) -> str:
        return "Propose assigning one inventory element of a service pool to a user"

    @typing.override
    def tool_description(self) -> str:
        return (
            "Propose assigning one EXISTING element of a service pool's inventory "
            "(physical machines, imported VMs, ...; only pools whose service type "
            "supports manual assignment) to one user. target_uuid is the POOL's "
            "uuid. The values are 'user_id' (uuid of the receiving user) and "
            "'assignable_id' (one element of the pool's inventory, discoverable "
            "with get_servicepool_assignables). The created user service starts in "
            "PREPARING and finishes asynchronously: the provider may still fail it. "
            "The proposal does NOT assign anything: it is queued until an "
            "administrator approves it."
        )

    # ---------------------------------------------------------- execution

    @typing.override
    async def op_create(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        def _plan() -> tuple[str, str, str, JsonObject]:
            pool = _pool_or_fail(action.target_uuid)
            values = dict(action.values)
            assignable_id = str(values.get("assignable_id", "")).strip()
            user = self._validate_user(values.get("user_id"))
            if isinstance(user, str):
                raise rest_exceptions.RequestError(f"{user} (it disappeared after the proposal was approved)")
            if not pool.service.get_type().can_assign():
                raise rest_exceptions.RequestError(
                    f'service pool "{pool.name}" does not support manual assignment anymore'
                )
            return (
                pool.name,
                pool.uuid,
                assignable_id,
                {"user_id": user.uuid, "assignable_id": assignable_id},
            )

        pool_name, pool_uuid, assignable_id, params = await sync_to_async(_plan, thread_sensitive=True)()
        # The REST handler answers with the created item's uuid (the
        # id key execute_create reads), so the message names the new row.
        new_uuid = await mutability_verbs.execute_create(
            RestTarget(
                ServicesPools,
                "services_pools",
                types.rest.CustomMethodMethod.POST,
                args=(pool_uuid, "create_from_assignable"),
            ),
            request,
            params,
        )
        return f'Service pool "{pool_name}" inventory element "{assignable_id}" assigned' + (
            f" (uuid {new_uuid})" if new_uuid else ""
        )
