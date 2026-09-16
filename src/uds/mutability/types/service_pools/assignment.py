"""``servicepool.assignment``: reassign, act on or detach an assigned user service.

Operations over the ``services`` detail collection of a pool, never
fields: ``update`` changes the owner of one assigned user service (the
PUT of the REST services detail, one user service per user and pool),
``custom`` applies one named verb to it — today only ``reset``, brought
back to its base state through the ``reset`` custom method — and
``delete`` releases it (soft delete that marks the service for removal
and frees it for reuse, exactly the DELETE the admin UI issues). Custom
verbs are *data*, not operations: the ``custom`` operation carries them
in the ``action`` CHOICE field, so a future verb (reboot, stop, …) is a
new entry in ``ACTIONS`` plus its execution branch, never a new tool or
registered type. Members are discovered through the external immutable
``list_services_pools_services`` list tool; the read view mirrors it plus
the pool's capability flags and the verbs the type supports.

``update`` is a synchronous database write, so its approval-time CAS
policy is DENY (owner changes anywhere in the pool invalidate pending
proposals). ``custom`` and ``delete`` run under FORCE: the outcome is
asynchronous (a reset may need an actor round-trip, releasing walks
USABLE -> REMOVING -> REMOVABLE through the cleaners), exactly like
publications. A pool whose type does not support resetting answers
NotSupportedError at REST (the gate added with the capability contract);
the same rule is checked here at propose time so a doomed proposal fails
early.
"""

import collections.abc
import dataclasses
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
from uds.REST.methods.user_services import AssignedUserService

from ... import base as mutability_base
from ...base import ActionOperation, StalePolicy

JsonObject = dict[str, typing.Any]

State = types.states.State

_RELEASABLE_STATES: typing.Final[frozenset[str]] = frozenset([State.USABLE, State.PREPARING, State.REMOVING])
#: States an owner reassignment accepts (a service being removed cannot
#: change owner; the REST PUT holds no state gate of its own).
_ASSIGNABLE_STATES: typing.Final[frozenset[str]] = frozenset([State.USABLE, State.PREPARING])
_CAS_STATES: typing.Final[frozenset[str]] = frozenset([*State.INFO_STATES, State.PREPARING])
_MAX_TOOLTIP_MEMBERS: typing.Final[int] = 10

UserServicesRow = dict[str, typing.Any]


@dataclasses.dataclass(frozen=True)
class AssignmentAction:
    """One verb the ``custom`` operation can apply to an assigned service.

    Declaring a verb is all the family needs: discovery (the CHOICE),
    early validation (capability + states) and execution (the custom
    method segment) read this entry, so growing the vocabulary is a new
    entry here plus — when the verb needs parameters or a different REST
    shape — a small execution branch.
    """

    name: str
    description: str  # tooltip text explaining the verb itself
    states: frozenset[str]  # assigned states the verb applies to
    states_hint: str  # human sentence naming those states
    capability: str | None  # PoolCapabilities attribute the type must enable
    summary: str  # word for the execution summary ("resetting")


RESET = AssignmentAction(
    name="reset",
    description="reset the service to its base state",
    states=frozenset([State.USABLE]),
    states_hint="only usable services can be reset",
    capability="can_reset",
    summary="resetting",
)

#: The verbs ``custom`` accepts, in proposal order.
ACTIONS: typing.Final[dict[str, AssignmentAction]] = {RESET.name: RESET}


def _supported_actions(pool: models.ServicePool) -> list[AssignmentAction]:
    """Verbs the pool's service type currently supports."""
    capabilities = pool.capabilities()
    return [
        action
        for action in ACTIONS.values()
        if action.capability is None or getattr(capabilities, action.capability)
    ]


def _user_services_rows(
    pool: models.ServicePool,
    for_cached: bool,
    limit: int | None = None,
) -> list[UserServicesRow]:
    """Live assigned/cached rows of the pool, newest first."""
    if for_cached:
        rows = pool.cached_users_services()
    else:
        rows = pool.assigned_user_services()
    rows = rows.order_by("-creation_date", "-id")
    if limit is not None:
        rows = rows[:limit]
    return [
        {
            "uuid": row.uuid,
            "friendly_name": row.friendly_name,
            "state": row.state,
            "state_label": State.from_str(row.state).localized,
            "in_use": row.in_use,
            "owner": row.user.pretty_name if row.user else None,
            "owner_uuid": row.user.uuid if row.user else None,
        }
        for row in rows
    ]


def _cas_rows(pool: models.ServicePool) -> list[UserServicesRow]:
    """Assigned rows in a state the operations care about.

    The owner travels in the snapshot so an owner change anywhere in the
    pool is visible to the CAS layer (``update`` runs DENY on it).
    """
    rows = pool.assigned_user_services().filter(state__in=list(_CAS_STATES)).order_by("uuid")
    return [{"uuid": row.uuid, "state": row.state, "user": row.user.uuid if row.user else None} for row in rows]


def _cas_state(pool: models.ServicePool) -> JsonObject:
    return {"user_services": _cas_rows(pool)}


class ServicePoolAssignment(mutability_base.MutableActionType):
    """Proposal: reassign, act on or release one assigned user service of a pool."""

    type_id = "servicepool.assignment"

    handler = ServicesPools
    model = models.ServicePool
    noun = "Service pool"
    # custom/delete verbs are asynchronous: the outcome is produced by actors
    # and the cleaners, so pending proposals stay valid while states advance,
    # execution re-reads and REST holds the authoritative checks. update is a
    # synchronous owner swap, so it rides the strict whole-item CAS instead.
    stale_policy = StalePolicy.FORCE
    stale_policies_overrides: typing.ClassVar[dict[ActionOperation, StalePolicy]] = {
        ActionOperation.UPDATE: StalePolicy.DENY
    }
    # Discovery without the pool is useless: the selectable services and
    # the verbs the type supports belong to the concrete pool.
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
        """The operation this instance is bound to (the family has three)."""
        if self.operation is None:
            raise ValueError(f"{self.type_id}: assignment types must be bound to an operation")
        return self.operation

    # ---------------------------------------------------------- discovery

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        user_service: JsonObject = {
            "name": "user_service",
            "type": types.ui.FieldType.TEXT.value,
            "label": "Assigned user service uuid",
            "tooltip": self._user_service_tooltip(target),
            "secret": False,
        }
        if self._op() is ActionOperation.DELETE:
            return [user_service]
        if self._op() is ActionOperation.UPDATE:
            return [
                user_service,
                {
                    "name": "user",
                    "type": types.ui.FieldType.TEXT.value,
                    "label": "New owner user uuid",
                    "tooltip": self._user_tooltip(target),
                    "secret": False,
                },
            ]
        return [
            {
                "name": "action",
                "type": types.ui.FieldType.CHOICE.value,
                "label": "Action to apply",
                "tooltip": self._action_tooltip(target),
                "choices": self._action_choices(target),
                "secret": False,
            },
            user_service,
        ]

    def _action_choices(self, target: db_models.Model | None) -> list[str]:
        if isinstance(target, models.ServicePool):
            return [action.name for action in _supported_actions(target)]
        return [action.name for action in ACTIONS.values()]

    def _action_tooltip(self, target: db_models.Model | None) -> str:
        listed = "; ".join(f"'{action.name}' {action.description}" for action in ACTIONS.values())
        base = f"verb to apply to the assigned user service (currently: {listed}). "
        if isinstance(target, models.ServicePool):
            supported = _supported_actions(target)
            if not supported:
                return base + "This pool's service type supports none of these actions."
            return base + f"This pool's service type supports: {', '.join(a.name for a in supported)}."
        return base

    def _user_service_tooltip(self, target: db_models.Model | None) -> str:
        if self._op() is ActionOperation.DELETE:
            base = (
                "uuid of the assigned user service to release (soft delete: it is marked "
                "for removal and freed for reuse; usable, preparing or removing services "
                "can be released). "
            )
            supported_states = _RELEASABLE_STATES
        elif self._op() is ActionOperation.UPDATE:
            base = (
                "uuid of the assigned user service whose owner will change (only usable "
                "or preparing services can be reassigned). "
            )
            supported_states: frozenset[str] = _ASSIGNABLE_STATES
        else:
            base = (
                "uuid of the assigned user service to act upon (the states the chosen "
                "action accepts depend on the action; see the action field). "
            )
            supported_states = frozenset(state for action in ACTIONS.values() for state in action.states)
        if isinstance(target, models.ServicePool):
            listed = [
                f"{row['uuid']} ({row['friendly_name']})"
                for row in _user_services_rows(target, for_cached=False)
                if row["state"] in supported_states
            ][:_MAX_TOOLTIP_MEMBERS]
            if listed:
                return base + f"Currently listed: {', '.join(listed)}."
            return (
                base
                + "This pool has no assigned user service in a state any of these actions accepts right now."
            )
        return base + "Discover the uuids with list_services_pools_services or get_servicepool_assignment."

    def _user_tooltip(self, target: db_models.Model | None) -> str:
        base = (
            "uuid of the User that will own the user service: a user can own at most one "
            "user service of this pool, so the target must not already own another one. "
        )
        if isinstance(target, models.ServicePool):
            listed = [
                f"{row['owner_uuid']} ({row['owner']})"
                for row in _user_services_rows(target, for_cached=False)
                if row["owner_uuid"]
            ][:_MAX_TOOLTIP_MEMBERS]
            if listed:
                return (
                    base + f"Current owners of this pool: {', '.join(listed)}. "
                    "Discover more users with list_authenticators_users."
                )
            return base + "Discover user uuids with list_authenticators_users."
        return base + "Discover user uuids with list_authenticators_users."

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
        if "user_service" not in flat:
            errors.append("field user_service is required (uuid of an assigned user service of this pool)")
            return errors
        if self._op() is ActionOperation.DELETE:
            errors.extend(self._validate_member(flat["user_service"], _RELEASABLE_STATES, "release", target))
            return errors
        if self._op() is ActionOperation.UPDATE:
            errors.extend(self._validate_member(flat["user_service"], _ASSIGNABLE_STATES, "reassign", target))
            validated_user = self._validate_user(flat.get("user"))
            if isinstance(validated_user, str):
                errors.append(validated_user)
            elif isinstance(target, models.ServicePool):
                # Same one-service-per-user rule the REST PUT enforces
                clash = (
                    target.userServices.filter(user=validated_user)
                    .exclude(uuid=process_uuid(typing.cast(str, flat["user_service"])))
                    .exclude(state__in=State.INFO_STATES)
                    .exists()
                )
                if clash:
                    errors.append(
                        f"user {validated_user.pretty_name} already owns another user service of this pool"
                    )
            return errors
        validated = self._validate_action_choice(flat.get("action"), target)
        if isinstance(validated, str):
            errors.append(validated)
        else:
            errors.extend(self._validate_member(flat["user_service"], validated.states, validated.name, target))
        return errors

    def _validate_user(self, value: typing.Any) -> "models.User | str":
        """The reassignment target user, or an error message."""
        if not isinstance(value, str):
            return "field user must be the uuid string of a user"
        try:
            uuid = process_uuid(value)
        except ValueError:
            return f"field user is not a valid uuid: {value!r}"
        user = models.User.objects.filter(uuid=uuid).first()
        if user is None:
            return f"user {value} does not exist"
        return user

    def _validate_action_choice(
        self, value: typing.Any, target: db_models.Model | None
    ) -> AssignmentAction | str:
        """The chosen action, or an error message."""
        if value is None:
            return "field action is required (the verb to apply, e.g. 'reset')"
        if not isinstance(value, str):
            return "field action must be one of the action literals (e.g. 'reset')"
        action = ACTIONS.get(value)
        if action is None:
            accepted = ", ".join(sorted(ACTIONS))
            return f"field action '{value}' is not supported (accepted: {accepted})"
        if isinstance(target, models.ServicePool) and action.name not in self._action_choices(target):
            return f"this service pool's service type does not support the '{action.name}' action"
        return action

    def _validate_member(
        self,
        value: typing.Any,
        states: frozenset[str],
        action: str,
        target: db_models.Model | None,
    ) -> list[str]:
        if not isinstance(value, str):
            return ["field user_service must be the uuid string of an assigned user service of this pool"]
        try:
            uuid = process_uuid(value)
        except ValueError:
            return [f"field user_service is not a valid uuid: {value!r}"]
        if target is None:
            return []
        pool = typing.cast(models.ServicePool, target)
        if self._op() is ActionOperation.CUSTOM:
            hint = (
                ACTIONS[action].states_hint
                if action in ACTIONS
                else "the service state does not accept the action"
            )
        elif self._op() is ActionOperation.UPDATE:
            hint = "only usable or preparing services can be reassigned"
        else:
            hint = "only usable, preparing or removing services can be released"
        row = pool.assigned_user_services().filter(uuid=uuid).first()
        if row is None:
            return [f"user service {uuid} is not an assigned service of this service pool"]
        if row.state not in states:
            return [
                f"user service {uuid} is in state {row.state} ({State.from_str(row.state).localized}); {hint}"
            ]
        return []

    # ------------------------------------------------------------- CAS

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        return ["user_services"]

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
            "supported_actions": [action.name for action in _supported_actions(pool)],
            "user_services": _user_services_rows(pool, for_cached=False),
        }

    @typing.override
    def read_title(self) -> str:
        return "Read service pool assigned user services"

    @typing.override
    def read_description(self) -> str:
        return (
            "Read the assigned user services of one service pool with their uuids, "
            "states and owners (owner names and owner_uuids, the latter being what "
            "reassignment proposals need), plus the pool's capability flags and the "
            "action literals its service type supports (reassigning the owner goes "
            "through the update proposal tool, custom verbs through the custom one, "
            "releasing through delete). Applies immediately; it is a plain read, "
            "never a proposal."
        )

    # -------------------------------------------------------- tool text

    @typing.override
    def tool_title(self) -> str:
        if self._op() is ActionOperation.UPDATE:
            return "Propose reassigning an assigned user service to a user"
        if self._op() is ActionOperation.CUSTOM:
            return "Propose an action on an assigned user service"
        return "Propose releasing an assigned user service"

    @typing.override
    def tool_description(self) -> str:
        pool = "service pool"
        if self._op() is ActionOperation.UPDATE:
            return (
                f"Propose to change the owner of an assigned user service of a {pool}. The "
                "'user_service' field is the uuid of an assigned user service of this pool "
                "in usable or preparing state, and the 'user' field is the uuid of the "
                "User that will own it; a user can own at most one user service of this "
                "pool. Read the current owners with get_servicepool_assignment or "
                "list_services_pools_services. The proposal does NOT apply anything: it "
                "is queued until an administrator approves it."
            )
        if self._op() is ActionOperation.CUSTOM:
            verbs = ", ".join(f"'{action.name}' ({action.description})" for action in ACTIONS.values())
            return (
                f"Propose to apply an action verb to an assigned user service of a {pool}. "
                f"The 'action' field chooses the verb — currently: {verbs} — and the "
                "'user_service' field is the uuid of an assigned user service of this "
                "pool in a state the verb accepts. Each verb is only offered when the "
                "pool's service type supports it. Read the current state and supported "
                "verbs with get_servicepool_assignment or list_services_pools_services. "
                "The proposal does NOT apply anything: it is queued until an "
                "administrator approves it."
            )
        return (
            f"Propose to release (detach) an assigned user service of a {pool}: it is "
            "marked for removal and freed for reuse, exactly like the DELETE on the "
            "REST services detail. The 'user_service' field is the uuid of an assigned "
            "user service of this pool in usable, preparing or removing state. Read the "
            "current state with get_servicepool_assignment or "
            "list_services_pools_services. The proposal does NOT apply anything: it is "
            "queued until an administrator approves it."
        )

    # ---------------------------------------------------------- execution

    @typing.override
    async def op_update(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        def _plan() -> tuple[str, str, str, JsonObject]:
            pool = typing.cast(models.ServicePool, self.resolve_target(action.target_uuid))
            values = self.flatten_values(action.values)
            member_uuid = _resolve_member(pool, values["user_service"], _ASSIGNABLE_STATES, "reassign")
            user = models.User.objects.filter(uuid=process_uuid(typing.cast(str, values["user"]))).first()
            if user is None:
                raise rest_exceptions.RequestError(
                    f"user {values['user']!r} no longer exists (it disappeared after the proposal was approved)"
                )
            # Same one-service-per-user rule the REST PUT enforces
            if (
                pool.userServices.filter(user=user)
                .exclude(uuid=member_uuid)
                .exclude(state__in=State.INFO_STATES)
                .exists()
            ):
                raise rest_exceptions.RequestError(
                    f"There is already another user service assigned to {user.pretty_name}"
                )
            # auth_id is the REST payload gate (it must match the user's
            # authenticator); the user_id uuid is the one actually resolved.
            return pool.name, pool.uuid, member_uuid, {"user_id": user.uuid, "auth_id": user.manager.uuid}

        pool_name, pool_uuid, member_uuid, params = await sync_to_async(_plan, thread_sensitive=True)()
        await self._dispatch(
            request,
            pool_uuid,
            (member_uuid,),
            params,
            method=types.rest.CustomMethodMethod.PUT,
        )
        return f'Service pool "{pool_name}" user service {member_uuid} ownership reassigned'

    @typing.override
    async def op_custom(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        verb = action.values.get("action")
        spec = ACTIONS.get(verb) if isinstance(verb, str) else None
        if spec is None:
            raise rest_exceptions.RequestError(
                f"action {verb!r} is not supported (accepted: {', '.join(sorted(ACTIONS))})"
            )

        def _plan() -> tuple[str, str]:
            pool = typing.cast(models.ServicePool, self.resolve_target(action.target_uuid))
            if spec.name not in [supported.name for supported in _supported_actions(pool)]:
                raise rest_exceptions.NotSupportedError(
                    f'Service pool "{pool.name}" does not support the "{spec.name}" action (its service type does not)'
                )
            return pool.name, _resolve_member(pool, action.values["user_service"], spec.states, spec.name)

        pool_name, user_service_uuid = await sync_to_async(_plan, thread_sensitive=True)()
        await self._dispatch(request, action.target_uuid, (user_service_uuid, spec.name))
        return f'Service pool "{pool_name}" user service {user_service_uuid} {spec.summary}'

    @typing.override
    async def op_delete(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        def _plan() -> tuple[str, str]:
            pool = typing.cast(models.ServicePool, self.resolve_target(action.target_uuid))
            return pool.name, _resolve_member(
                pool, action.values["user_service"], _RELEASABLE_STATES, "release"
            )

        pool_name, user_service_uuid = await sync_to_async(_plan, thread_sensitive=True)()
        await self._dispatch(request, action.target_uuid, (user_service_uuid,))
        return f'Service pool "{pool_name}" user service {user_service_uuid} releasing'

    @staticmethod
    async def _dispatch(
        request: ExtendedHttpRequestWithUser,
        pool_uuid: str,
        args: tuple[str, ...],
        params: JsonObject | None = None,
        method: "types.rest.CustomMethodMethod | None" = None,
    ) -> typing.Any:
        """The REST services-detail call of the family.

        Defaults to the custom-method POST when the args carry a verb and to
        DELETE when they do not; explicit verbs (the update PUT) pass
        ``method``.
        """
        if method is None:
            method = (
                types.rest.CustomMethodMethod.POST if len(args) > 1 else types.rest.CustomMethodMethod.DELETE
            )
        return await RestProxy().execute(
            RestTarget(
                AssignedUserService,
                "services_pools/{uuid}/services",
                method,
                args=args,
                parent=RestTarget(ServicesPools, "services_pools"),
            ),
            request,
            params or {},
            parent_uuid=pool_uuid,
        )


def _resolve_member(pool: models.ServicePool, value: typing.Any, states: frozenset[str], action: str) -> str:
    """Sync-boundary check of the approved member; returns its uuid."""
    uuid = process_uuid(typing.cast(str, value))
    row = pool.assigned_user_services().filter(uuid=uuid).first()
    if row is None:
        raise rest_exceptions.RequestError(
            f'Service pool "{pool.name}" no longer has assigned user service {uuid} '
            "(it disappeared after the proposal was approved)"
        )
    if row.state not in states:
        raise rest_exceptions.RequestError(
            f"assigned user service {uuid} is in state {row.state} "
            f"({State.from_str(row.state).localized}); {action} is not applicable to it anymore"
        )
    return uuid
