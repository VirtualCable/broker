"""``metapool.assignment``: reassign, act on or detach a meta pool assigned service.

Operations over the ``services`` detail collection of a meta pool,
never fields. A meta pool has no assigned services of its own: the
collection is the projection of the assigned (cache_level=0) user
services of its member pools, so every operation resolves the target
user service through that projection (404 outside it, exactly like the
REST detail does). ``update`` changes the owner of one assigned user
service (the PUT of the REST services detail; the one-service-per-user
rule is enforced per member pool service, as the REST does), ``custom``
applies one named verb to it — today only ``reset``, through the ``reset``
custom method, gated by the capability of the member pool's service —
and ``delete`` releases it (release/cancel depending on the state, the
DELETE the admin UI issues). Custom verbs are *data*, not operations:
the ``custom`` operation carries them in the ``action`` CHOICE field.

``update`` is a synchronous database write, so its approval-time CAS
policy is DENY (owner changes anywhere in the projection invalidate
pending proposals). ``custom`` and ``delete`` run under FORCE: the
outcome is asynchronous (a reset may need an actor round-trip, releasing
walks USABLE -> REMOVING -> REMOVABLE through the cleaners), exactly
like publications and the service pool assignment family.
"""

import collections.abc
import dataclasses
import hashlib
import json
import typing

from asgiref.sync import sync_to_async
from django.db import models as db_models
from django.db.models.query import QuerySet

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.core.util.model import process_uuid
from uds.mcp.rest_proxy import RestProxy, RestTarget
from uds.REST.methods.meta_pools import MetaPools
from uds.REST.methods.meta_service_pools import MetaAssignedService

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
    capability: str  # PoolCapabilities attribute the member service must enable
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


def _member_pools(meta_pool: models.MetaPool) -> list[models.ServicePool]:
    """The service pools the meta pool is made of (all of them)."""
    return [member.pool for member in meta_pool.members.select_related("pool")]


def _meta_assigned_services(meta_pool: models.MetaPool) -> "QuerySet[models.UserService]":
    """The projection the REST ``services`` detail resolves members against."""
    return models.UserService.objects.filter(
        cache_level=0,
        deployed_service__in=_member_pools(meta_pool),
    )


def _supports(action: AssignmentAction, row: models.UserService) -> bool:
    """Whether the member service behind one row supports the verb."""
    return bool(getattr(row.deployed_service.capabilities(), action.capability))


def _user_services_rows(meta_pool: models.MetaPool) -> list[UserServicesRow]:
    """Live assigned rows of the enabled member pools, newest first.

    Mirrors the REST listing (enabled members only, VALID_STATES) and
    adds the member pool identity plus the per-row verbs, since both
    depend on WHICH member pool backs the service.
    """
    rows: list[UserServicesRow] = []
    for member in meta_pool.members.filter(enabled=True).select_related("pool"):
        for row in (
            member.pool.assigned_user_services()
            .filter(state__in=State.VALID_STATES)
            .order_by("-creation_date", "-id")
        ):
            rows.append(
                {
                    "uuid": row.uuid,
                    "friendly_name": row.friendly_name,
                    "state": row.state,
                    "state_label": State.from_str(row.state).localized,
                    "in_use": row.in_use,
                    "owner": row.user.pretty_name if row.user else None,
                    "owner_uuid": row.user.uuid if row.user else None,
                    "pool_id": member.pool.uuid,
                    "pool_name": member.pool.name,
                    "supported_actions": [action.name for action in ACTIONS.values() if _supports(action, row)],
                }
            )
    return rows


def _cas_rows(meta_pool: models.MetaPool) -> list[UserServicesRow]:
    """Assigned rows (whole projection) in a state the operations care about.

    Owner and member pool travel in the snapshot so an owner change — or
    a membership change moving services in or out of the projection — is
    visible to the CAS layer (``update`` runs DENY on it).
    """
    rows = (
        _meta_assigned_services(meta_pool)
        .filter(state__in=list(_CAS_STATES))
        .select_related("user", "deployed_service")
        .order_by("uuid")
    )
    return [
        {
            "uuid": row.uuid,
            "state": row.state,
            "user": row.user.uuid if row.user else None,
            "pool": row.deployed_service.uuid,
        }
        for row in rows
    ]


def _cas_state(meta_pool: models.MetaPool) -> JsonObject:
    return {"user_services": _cas_rows(meta_pool)}


class MetaPoolAssignment(mutability_base.MutableActionType):
    """Proposal: reassign, act on or release one meta pool assigned service."""

    type_id = "metapool.assignment"

    handler = MetaPools
    model = models.MetaPool
    noun = "Meta pool"
    # custom/delete verbs are asynchronous: the outcome is produced by actors
    # and the cleaners, so pending proposals stay valid while states advance,
    # execution re-reads and REST holds the authoritative checks. update is a
    # synchronous owner swap, so it rides the strict whole-item CAS instead.
    stale_policy = StalePolicy.FORCE
    stale_policies_overrides: typing.ClassVar[dict[ActionOperation, StalePolicy]] = {
        ActionOperation.UPDATE: StalePolicy.DENY
    }
    # Discovery without the meta pool is useless: the selectable services
    # (the projection of the member pools) belong to the concrete meta pool.
    target_scoped_fields = True

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.MetaPool.objects.get(uuid__iexact=target_uuid)
        except models.MetaPool.DoesNotExist:
            raise rest_exceptions.NotFound("Meta pool not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return "metapool"

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
                "choices": [action.name for action in ACTIONS.values()],
                "secret": False,
            },
            user_service,
        ]

    def _action_tooltip(self, target: db_models.Model | None) -> str:
        listed = "; ".join(f"'{action.name}' {action.description}" for action in ACTIONS.values())
        return (
            f"verb to apply to the assigned user service (currently: {listed}). Whether a "
            "service accepts a verb depends on the member pool service behind it; each "
            "row of get_metapool_assignment lists its supported actions."
            if isinstance(target, models.MetaPool)
            else (
                f"verb to apply to the assigned user service (currently: {listed}). Whether a "
                "service accepts a verb depends on the member pool service behind it."
            )
        )

    def _user_service_tooltip(self, target: db_models.Model | None) -> str:
        if self._op() is ActionOperation.DELETE:
            base = (
                "uuid of the meta pool assigned service to release (soft delete: it is "
                "marked for removal and freed for reuse; usable, preparing or removing "
                "services can be released). "
            )
            supported_states = _RELEASABLE_STATES
        elif self._op() is ActionOperation.UPDATE:
            base = (
                "uuid of the meta pool assigned service whose owner will change (only "
                "usable or preparing services can be reassigned). "
            )
            supported_states: frozenset[str] = _ASSIGNABLE_STATES
        else:
            base = (
                "uuid of the meta pool assigned service to act upon (the states the "
                "chosen action accepts depend on the action; see the action field). "
            )
            supported_states = frozenset(state for action in ACTIONS.values() for state in action.states)
        if isinstance(target, models.MetaPool):
            listed = [
                f"{row['uuid']} ({row['pool_name']}/{row['friendly_name']})"
                for row in _user_services_rows(target)
                if row["state"] in supported_states
            ][:_MAX_TOOLTIP_MEMBERS]
            if listed:
                return base + f"Currently listed: {', '.join(listed)}."
            return (
                base
                + "This meta pool has no assigned service in a state any of these actions accepts right now."
            )
        return base + "Discover the uuids with get_metapool_assignment."

    def _user_tooltip(self, target: db_models.Model | None) -> str:
        base = (
            "uuid of the User that will own the user service: a user can own at most one "
            "user service of each member pool, so the target must not already own another "
            "one backed by the same member pool. "
        )
        if isinstance(target, models.MetaPool):
            listed = [
                f"{row['owner_uuid']} ({row['owner']})"
                for row in _user_services_rows(target)
                if row["owner_uuid"]
            ][:_MAX_TOOLTIP_MEMBERS]
            if listed:
                return (
                    base + f"Current owners of this meta pool: {', '.join(listed)}. "
                    "Discover more users with list_authenticators_users."
                )
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
            errors.append("field user_service is required (uuid of an assigned service of this meta pool)")
            return errors
        if self._op() is ActionOperation.DELETE:
            errors.extend(self._validate_member(flat["user_service"], _RELEASABLE_STATES, "release", target))
            return errors
        if self._op() is ActionOperation.UPDATE:
            errors.extend(self._validate_member(flat["user_service"], _ASSIGNABLE_STATES, "reassign", target))
            validated_user = self._validate_user(flat.get("user"))
            if isinstance(validated_user, str):
                errors.append(validated_user)
            elif isinstance(target, models.MetaPool):
                row = self._find_member(target, flat["user_service"])
                # Same one-service-per-member-service rule the REST PUT enforces
                if (
                    row is not None
                    and row.state in _ASSIGNABLE_STATES
                    and row.deployed_service.userServices.filter(user=validated_user)
                    .exclude(uuid=row.uuid)
                    .exclude(state__in=State.INFO_STATES)
                    .exists()
                ):
                    errors.append(
                        f"user {validated_user.pretty_name} already owns another user service "
                        f"of member pool {row.deployed_service.name}"
                    )
            return errors
        validated = self._validate_action_choice(flat.get("action"), target, flat["user_service"])
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
        self, value: typing.Any, target: db_models.Model | None, user_service: typing.Any
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
        if isinstance(target, models.MetaPool):
            # Capability is per member service: refuse early when the
            # concrete target row is known and its service lacks the verb
            row = self._find_member(target, user_service)
            if row is not None and not _supports(action, row):
                return (
                    f"the member pool service behind user service {row.uuid} does not "
                    f"support the '{action.name}' action"
                )
        return action

    def _validate_member(
        self,
        value: typing.Any,
        states: frozenset[str],
        action: str,
        target: db_models.Model | None,
    ) -> list[str]:
        if not isinstance(value, str):
            return ["field user_service must be the uuid string of an assigned service of this meta pool"]
        try:
            uuid = process_uuid(value)
        except ValueError:
            return [f"field user_service is not a valid uuid: {value!r}"]
        if target is None:
            return []
        meta_pool = typing.cast(models.MetaPool, target)
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
        row = _meta_assigned_services(meta_pool).filter(uuid=uuid).first()
        if row is None:
            return [f"user service {uuid} is not an assigned service of this meta pool"]
        if row.state not in states:
            return [
                f"user service {uuid} is in state {row.state} ({State.from_str(row.state).localized}); {hint}"
            ]
        return []

    def _find_member(self, meta_pool: models.MetaPool, value: typing.Any) -> models.UserService | None:
        """The projected row for a raw uuid (or None)."""
        if not isinstance(value, str):
            return None
        try:
            return _meta_assigned_services(meta_pool).filter(uuid=process_uuid(value)).first()
        except ValueError:
            return None

    # ------------------------------------------------------------- CAS

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        return ["user_services"]

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        meta_pool = typing.cast(models.MetaPool, target)
        wanted = set(names)
        cas = _cas_state(meta_pool)
        return {name: value for name, value in cas.items() if name in wanted}

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        cas = _cas_state(typing.cast(models.MetaPool, target))
        return hashlib.sha256(json.dumps(cas, sort_keys=True).encode("utf-8")).hexdigest()

    @typing.override
    def target_display_name(self, target: db_models.Model) -> str:
        return typing.cast(models.MetaPool, target).name

    # ------------------------------------------------------------ read

    @typing.override
    def read(self, target_uuid: str) -> JsonObject:
        meta_pool = typing.cast(models.MetaPool, self.resolve_target(target_uuid))
        return {
            "entity_type": self.type_id,
            "target_uuid": self.target_uuid_of(meta_pool),
            "for_type": "metapool",
            "member_pools": [
                {"uuid": member.pool.uuid, "name": member.pool.name, "enabled": member.enabled}
                for member in meta_pool.members.select_related("pool")
            ],
            "user_services": _user_services_rows(meta_pool),
        }

    @typing.override
    def read_title(self) -> str:
        return "Read meta pool assigned user services"

    @typing.override
    def read_description(self) -> str:
        return (
            "Read the assigned user services of one meta pool — the projection of its "
            "member pools — with their uuids, states, owners (owner names and "
            "owner_uuids, the latter being what reassignment proposals need), the "
            "member pool each one is backed by and the action verbs its member service "
            "supports (reassigning the owner goes through the update proposal tool, "
            "custom verbs through the custom one, releasing through delete). Applies "
            "immediately; it is a plain read, never a proposal."
        )

    # -------------------------------------------------------- tool text

    @typing.override
    def tool_title(self) -> str:
        if self._op() is ActionOperation.UPDATE:
            return "Propose reassigning a meta pool assigned service to a user"
        if self._op() is ActionOperation.CUSTOM:
            return "Propose an action on a meta pool assigned service"
        return "Propose releasing a meta pool assigned service"

    @typing.override
    def tool_description(self) -> str:
        pool = "meta pool"
        if self._op() is ActionOperation.UPDATE:
            return (
                f"Propose to change the owner of an assigned service of a {pool}. The "
                "'user_service' field is the uuid of an assigned service of this meta "
                "pool in usable or preparing state, and the 'user' field is the uuid of "
                "the User that will own it; a user can own at most one user service per "
                "member pool. Read the current owners with get_metapool_assignment. The "
                "proposal does NOT apply anything: it is queued until an administrator "
                "approves it."
            )
        if self._op() is ActionOperation.CUSTOM:
            verbs = ", ".join(f"'{action.name}' ({action.description})" for action in ACTIONS.values())
            return (
                f"Propose to apply an action verb to an assigned service of a {pool}. "
                f"The 'action' field chooses the verb — currently: {verbs} — and the "
                "'user_service' field is the uuid of an assigned service of this meta "
                "pool in a state the verb accepts. Each verb is only available when the "
                "member pool service behind the target service supports it (each row of "
                "get_metapool_assignment lists its supported actions). The proposal "
                "does NOT apply anything: it is queued until an administrator approves it."
            )
        return (
            f"Propose to release (detach) an assigned service of a {pool}: it is released "
            "or cancelled depending on its state, exactly like the DELETE on the REST "
            "services detail. The 'user_service' field is the uuid of an assigned "
            "service of this meta pool in usable, preparing or removing state. Read the "
            "current state with get_metapool_assignment. The proposal does NOT apply "
            "anything: it is queued until an administrator approves it."
        )

    # ---------------------------------------------------------- execution

    @typing.override
    async def op_update(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        def _plan() -> tuple[str, str, str, JsonObject]:
            meta_pool = typing.cast(models.MetaPool, self.resolve_target(action.target_uuid))
            values = self.flatten_values(action.values)
            row = _resolve_member(meta_pool, values["user_service"], _ASSIGNABLE_STATES, "reassign")
            user = models.User.objects.filter(uuid=process_uuid(typing.cast(str, values["user"]))).first()
            if user is None:
                raise rest_exceptions.RequestError(
                    f"user {values['user']!r} no longer exists (it disappeared after the proposal was approved)"
                )
            # Same one-service-per-member-service rule the REST PUT enforces
            if (
                row.deployed_service.userServices.filter(user=user)
                .exclude(uuid=row.uuid)
                .exclude(state__in=State.INFO_STATES)
                .exists()
            ):
                raise rest_exceptions.RequestError(
                    f"There is already another user service assigned to {user.pretty_name}"
                )
            # auth_id is the REST payload gate (it must match the user's
            # authenticator); the user_id uuid is the one actually resolved.
            return (
                meta_pool.name,
                meta_pool.uuid,
                row.uuid,
                {"user_id": user.uuid, "auth_id": user.manager.uuid},
            )

        meta_pool_name, meta_pool_uuid, member_uuid, params = await sync_to_async(
            _plan, thread_sensitive=True
        )()
        await self._dispatch(
            request,
            meta_pool_uuid,
            (member_uuid,),
            params,
            method=types.rest.CustomMethodMethod.PUT,
        )
        return f'Meta pool "{meta_pool_name}" user service {member_uuid} ownership reassigned'

    @typing.override
    async def op_custom(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        verb = action.values.get("action")
        spec = ACTIONS.get(verb) if isinstance(verb, str) else None
        if spec is None:
            raise rest_exceptions.RequestError(
                f"action {verb!r} is not supported (accepted: {', '.join(sorted(ACTIONS))})"
            )

        def _plan() -> tuple[str, str]:
            meta_pool = typing.cast(models.MetaPool, self.resolve_target(action.target_uuid))
            row = _resolve_member(meta_pool, action.values["user_service"], spec.states, spec.name)
            if not _supports(spec, row):
                raise rest_exceptions.NotSupportedError(
                    f"the member pool service behind user service {row.uuid} does not support "
                    f'the "{spec.name}" action'
                )
            return meta_pool.name, row.uuid

        meta_pool_name, user_service_uuid = await sync_to_async(_plan, thread_sensitive=True)()
        await self._dispatch(request, action.target_uuid, (user_service_uuid, spec.name))
        return f'Meta pool "{meta_pool_name}" user service {user_service_uuid} {spec.summary}'

    @typing.override
    async def op_delete(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        def _plan() -> tuple[str, str]:
            meta_pool = typing.cast(models.MetaPool, self.resolve_target(action.target_uuid))
            row = _resolve_member(meta_pool, action.values["user_service"], _RELEASABLE_STATES, "release")
            return meta_pool.name, row.uuid

        meta_pool_name, user_service_uuid = await sync_to_async(_plan, thread_sensitive=True)()
        await self._dispatch(request, action.target_uuid, (user_service_uuid,))
        return f'Meta pool "{meta_pool_name}" user service {user_service_uuid} releasing'

    @staticmethod
    async def _dispatch(
        request: ExtendedHttpRequestWithUser,
        meta_pool_uuid: str,
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
                MetaAssignedService,
                "meta_pools/{uuid}/services",
                method,
                args=args,
                parent=RestTarget(MetaPools, "meta_pools"),
            ),
            request,
            params or {},
            parent_uuid=meta_pool_uuid,
        )


def _resolve_member(
    meta_pool: models.MetaPool, value: typing.Any, states: frozenset[str], action: str
) -> models.UserService:
    """Sync-boundary check of the approved member; returns the row."""
    uuid = process_uuid(typing.cast(str, value))
    row = _meta_assigned_services(meta_pool).filter(uuid=uuid).select_related("deployed_service").first()
    if row is None:
        raise rest_exceptions.RequestError(
            f'Meta pool "{meta_pool.name}" no longer has assigned user service {uuid} '
            "(it disappeared after the proposal was approved)"
        )
    if row.state not in states:
        raise rest_exceptions.RequestError(
            f"assigned user service {uuid} is in state {row.state} "
            f"({State.from_str(row.state).localized}); {action} is not applicable to it anymore"
        )
    return row
