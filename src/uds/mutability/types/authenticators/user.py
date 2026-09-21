"""``user.update`` / ``user.custom``: propose modifications to an existing user.

Second level resource (authenticators detail): the proposal only needs
the user uuid — the parent authenticator is derived from the ``manager``
FK at execution time (an user never changes authenticator), exactly like
``calendar_rule.update`` does for rules. Permissions are MANAGEMENT over
the authenticator, inherited through :meth:`permission_target`.

``custom`` is the entity-scoped verb binding of the same family: the
``action`` CHOICE names the verb to apply (today only ``clean_related``,
the MCP shape of the administration interface's "clean related data",
which resets the user's MFA), so growing the vocabulary is a new entry
in :data:`ACTIONS`, not a new operation. The verb name doubles as the
REST custom-method segment it dispatches to.

The users PUT is form-shaped and requires ``name``, ``real_name``,
``comments``, ``state``, ``staff_member`` and ``is_admin``; ``password``
and ``mfa_data`` are optional (absent on the PUT = keep current).

``staff_member`` and ``is_admin`` are privilege escalation vectors, so
they are NOT proposable: they travel as immutable context in every
payload (the PUT requires them) but the proposal cannot touch them.

The ``token``/``last_access``/``parent`` columns are NOT part of the
mutable surface.

The ``groups`` membership (m2m) IS part of the surface — on INTERNAL
authenticators only: external ones sync their users (and their groups)
from the provider, so there the relation is neither proposable nor part
of the CAS snapshot (it changes on its own, which would revoke actions
spuriously). The REST handler mirrors this: it only applies membership
on internal authenticators, non-child users, and skips meta groups.

``password`` is write-only: never snapshotted (CAS cannot observe it),
never sent unless the proposal carries it. The REST handler hashes it.
"""

import collections.abc
import dataclasses
import typing

from asgiref.sync import sync_to_async
from django.db import models as db_models

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.mcp.rest_proxy import RestProxy, RestTarget
from uds.REST.methods.authenticators import Authenticators
from uds.REST.methods.users_groups import Users

from ... import base as mutability_base
from ... import verbs as mutability_verbs
from ...etag import item_etag

JsonObject = dict[str, typing.Any]

# Snapshot keys: everything the PUT carries except the write-only
# password. staff_member/is_admin are required context (privilege
# escalation vectors, NOT proposable)
_USER_FIELDS: typing.Final[list[str]] = [
    "name",
    "real_name",
    "comments",
    "state",
    "staff_member",
    "is_admin",
    "mfa_data",
    "groups",
]


def _as_uuid_list(value: typing.Any) -> list[str]:
    """Normalize a proposal value into a uuid list (accepts list or csv)."""
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v) for v in value]


@dataclasses.dataclass(frozen=True)
class UserAction:
    """One verb the ``custom`` binding of the user family can apply.

    The verb name doubles as the REST custom-method segment it dispatches
    to, so growing the vocabulary is a new entry here (plus, when a verb
    needs parameters or a different REST shape, a small execution
    branch), exactly like the assignment family's verbs.
    """

    name: str
    description: str  # tooltip text explaining the verb itself
    requires_mfa: bool  # the user's authenticator must have an MFA bound
    summary: str  # word for the execution summary ("related data cleaned")


CLEAN_RELATED = UserAction(
    name="clean_related",
    description="reset the user's related external data, currently its MFA data",
    requires_mfa=True,
    summary="related data cleaned",
)

#: The verbs ``user.custom`` accepts, in proposal order.
ACTIONS: typing.Final[dict[str, UserAction]] = {CLEAN_RELATED.name: CLEAN_RELATED}


class UserUpdate(mutability_base.MutableActionType):
    """Proposal: update an existing user."""

    type_id = "user"
    title = "Propose user update"
    description = (
        "Propose changes to an existing user of an authenticator. The proposal does "
        "NOT apply anything: it is queued until an administrator approves it. Mutable "
        "fields are name (username), real_name, comments, state, mfa_data, password "
        "(write-only: only sent when proposed, and hashed by the broker) and, on "
        "internal authenticators only, the group membership (groups, uuid list; "
        "external ones sync it from the provider). "
        "The staff/admin privileges cannot be granted through proposals."
    )
    model = models.User
    noun = "User"
    # A creation is a detail of the parent authenticator: the proposal
    # targets the authenticator uuid and MANAGEMENT over it is the
    # create permission.
    create_needs_parent = True
    # The custom verbs are idempotent and depend only on what the verb
    # itself needs (an MFA bound for clean_related), never on the rest of
    # the user row, so unrelated drift between proposal and approval must
    # not revoke them; execution re-validates and REST holds the
    # authoritative checks.
    stale_policies_overrides: typing.ClassVar[
        dict[mutability_base.ActionOperation, mutability_base.StalePolicy]
    ] = {mutability_base.ActionOperation.CUSTOM: mutability_base.StalePolicy.FORCE}

    @typing.override
    def permission_target(self, target: db_models.Model) -> db_models.Model:
        # A user is a detail of its authenticator: MANAGEMENT is held over
        # the parent, exactly like the REST dispatcher checks it.
        return typing.cast(models.User, target).manager

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.User.objects.get(uuid__iexact=target_uuid)
        except models.User.DoesNotExist:
            raise rest_exceptions.NotFound("User not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return "user"

    def membership_editable(self, target: db_models.Model | None) -> bool:
        """Whether the groups membership is part of the surface.

        Only on internal authenticators: external ones sync their users
        (and their groups) from the provider. Without a target (creation
        or discovery before choosing one) the field is kept with its
        own tooltip explaining the restriction.
        """
        if target is None:
            return True
        user = typing.cast(models.User, target)
        return not user.manager.get_instance().external_source

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        if self.operation is mutability_base.ActionOperation.CUSTOM:
            return [self._action_definition(target)]
        return [
            {
                "name": "name",
                "type": "text",
                "label": "Username",
                "tooltip": "Login name of the user",
                "secret": False,
            },
            {
                "name": "real_name",
                "type": "text",
                "label": "Name",
                "tooltip": "Real name of the user",
                "secret": False,
            },
            {
                "name": "comments",
                "type": "text",
                "label": "Comments",
                "tooltip": "Comments of the user",
                "secret": False,
            },
            {
                "name": "state",
                "type": "text",
                "label": "State",
                "tooltip": "One of: A (active), I (inactive), B (blocked)",
                "secret": False,
            },
            {
                "name": "mfa_data",
                "type": "text",
                "label": "MFA data",
                "tooltip": "MFA validation data (optional, empty keeps the current one)",
                "secret": False,
            },
            *(
                [
                    {
                        "name": "groups",
                        "type": "text",
                        "label": "Groups",
                        "tooltip": "Comma separated uuids of the direct groups the user belongs to "
                        "(internal authenticators only: external ones sync their membership from "
                        "the provider; meta groups are ignored). Replaces the current membership, "
                        "like the administration interface does.",
                        "secret": False,
                    }
                ]
                if self.membership_editable(target)
                else []
            ),
            {
                "name": "password",
                "type": "password",
                "label": "Password",
                "tooltip": "Write-only: omit to keep the current password (hashed on apply)",
                "secret": True,
            },
        ]

    def _action_definition(self, target: db_models.Model | None) -> JsonObject:
        """The CHOICE field naming the verb to apply, scoped to the target.

        Without a target the whole vocabulary is offered; with one, only
        the verbs it currently applies to (a user whose authenticator has
        no MFA bound cannot have its related data cleaned). An empty
        choices list is the discovery signal that nothing applies: the
        tooltip says why.
        """
        listed = "; ".join(f"'{action.name}' ({action.description})" for action in ACTIONS.values())
        supported = [action.name for action in ACTIONS.values() if self._verb_applies(action, target)]
        target_note = (
            f"This user's authenticator supports: {', '.join(supported)}."
            if supported
            else "This user's authenticator supports none of these actions (no MFA bound)."
        )
        return {
            "name": "action",
            "type": types.ui.FieldType.CHOICE.value,
            "label": "Action to apply",
            "tooltip": f"verb to apply to the user (currently: {listed}). {target_note}",
            "choices": supported,
            "secret": False,
        }

    @staticmethod
    def _verb_applies(action: UserAction, target: db_models.Model | None) -> bool:
        """Whether the verb applies to the target user right now."""
        if target is None or not action.requires_mfa:
            return True
        return typing.cast(models.User, target).manager.mfa is not None

    @typing.override
    def validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        if self.operation is not mutability_base.ActionOperation.CUSTOM:
            return super().validate_values(for_type, values, target)
        errors = super().validate_values(for_type, values, target)
        flat = self.flatten_values(values)
        action_name = flat.get("action")
        if action_name is None:
            errors.append("field action is required (the verb to apply, e.g. 'clean_related')")
            return errors
        action = ACTIONS.get(action_name) if isinstance(action_name, str) else None
        if action is None:
            accepted = ", ".join(sorted(ACTIONS))
            errors.append(f"field action {action_name!r} is not supported (accepted: {accepted})")
        elif not self._verb_applies(action, target):
            errors.append(f"the '{action.name}' action does not apply to this user (no MFA bound)")
        return errors

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        user = typing.cast(models.User, target)
        wanted = set(names)
        snapshot: JsonObject = {}
        for name in wanted:
            if name == "name":
                snapshot[name] = user.name
            elif name == "real_name":
                snapshot[name] = user.real_name
            elif name == "comments":
                snapshot[name] = user.comments
            elif name == "state":
                snapshot[name] = user.state
            elif name == "staff_member":
                snapshot[name] = user.staff_member
            elif name == "is_admin":
                snapshot[name] = user.is_admin
            elif name == "mfa_data":
                snapshot[name] = user.mfa_data
            elif name == "groups":
                snapshot[name] = [g.uuid for g in user.groups.all()]
        return snapshot

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        # On external authenticators the membership is provider synced:
        # outside the CAS fingerprint (it drifts on its own)
        fields = list(_USER_FIELDS)
        if not self.membership_editable(target):
            fields.remove("groups")
        return fields

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        fields = self.etag_fields("user")
        item_dict: JsonObject = self.snapshot_values(target, fields)
        return item_etag(item_dict, fields)

    # ---------------------------------------------------------- execution

    @typing.override
    def tool_title(self) -> str:
        if self.operation is mutability_base.ActionOperation.CREATE:
            return mutability_verbs.create_tool_title("user")
        if self.operation is mutability_base.ActionOperation.DELETE:
            return mutability_verbs.delete_tool_title("user")
        if self.operation is mutability_base.ActionOperation.CUSTOM:
            return "Propose an action on a user"
        return self.title

    @typing.override
    def tool_description(self) -> str:
        if self.operation is mutability_base.ActionOperation.CREATE:
            return mutability_verbs.create_tool_description(
                "user",
                "name (username), real_name, comments, state, mfa_data, password "
                "(write-only: hashed on apply) and groups (optional uuid list, "
                "internal authenticators only: external ones sync membership from "
                "the provider). Privileges (staff/admin) are never granted from "
                "proposals: they always start disabled.",
                needs_parent=True,
            )
        if self.operation is mutability_base.ActionOperation.DELETE:
            return mutability_verbs.delete_tool_description(
                "user of an authenticator",
                "the user is removed and its active assigned services are "
                "cancelled, like the administration interface does.",
            )
        if self.operation is mutability_base.ActionOperation.CUSTOM:
            verbs = ", ".join(f"'{action.name}' ({action.description})" for action in ACTIONS.values())
            return (
                "Propose to apply an action verb to a user. The 'action' field chooses the verb "
                f"— currently: {verbs} — exactly like the corresponding button of the "
                "administration interface. The clean_related verb is the fix for MFA problems: "
                "it erases the user's stored MFA data so the next login enrolls again, and it "
                "applies only when the user's authenticator has an MFA bound. The proposal does "
                "NOT apply anything: it is queued until an administrator approves it."
            )
        return self.description

    # ---------------------------------------------------- creation hooks

    @typing.override
    def resolve_create_parent(self, parent_uuid: str) -> db_models.Model:
        # The container of a user creation is its authenticator
        try:
            return models.Authenticator.objects.get(uuid__iexact=parent_uuid)
        except models.Authenticator.DoesNotExist:
            raise rest_exceptions.NotFound("Authenticator not found") from None

    @typing.override
    def create_validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        # Users have no subtype gallery: the data_type is not required
        return super().create_validate_values(for_type or "user", values, target)

    @typing.override
    async def op_create(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The users POST is form-shaped and the basic columns are
        # required: safe defaults for the ones a proposal omits.
        # Privileges (staff_member/is_admin) are NEVER granted from a
        # proposal: they always start disabled, exactly like the handler
        # forces for non-admin callers. ALL the ORM work stays out of the
        # async context.
        def _build_params() -> tuple[str, str, JsonObject]:
            authenticator = typing.cast(models.Authenticator, self.resolve_create_parent(action.target_uuid))
            values = dict(action.values)
            params: JsonObject = {
                "name": values.get("name"),
                "real_name": values.get("real_name", ""),
                "comments": values.get("comments", ""),
                "state": values.get("state", "A"),
                "staff_member": False,
                "is_admin": False,
            }
            if values.get("password"):
                params["password"] = values["password"]
            if values.get("mfa_data"):
                params["mfa_data"] = str(values["mfa_data"]).strip()
            # Membership only applies on internal authenticators (external
            # ones sync their membership from the provider)
            if values.get("groups") and not authenticator.get_instance().external_source:
                params["groups"] = _as_uuid_list(values["groups"])
            return str(params["name"]), authenticator.uuid, params

        name, authenticator_uuid, params = await sync_to_async(_build_params, thread_sensitive=True)()
        new_uuid = await mutability_verbs.execute_create(
            RestTarget(
                Users,
                "authenticators/{uuid}/users",
                types.rest.CustomMethodMethod.POST,
                parent=RestTarget(Authenticators, "authenticators"),
            ),
            request,
            params,
            authenticator_uuid,
        )
        return mutability_verbs.created_message("User", name, new_uuid)

    @typing.override
    async def op_delete(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The REST DELETE removes the user and cancels its active
        # assigned services. ALL the ORM work stays out of the async
        # context, including the parent lookup (the manager FK).
        def _resolve() -> tuple[str, str]:
            user = typing.cast(models.User, self.resolve_target(action.target_uuid))
            return user.name, user.manager.uuid

        name, authenticator_uuid = await sync_to_async(_resolve, thread_sensitive=True)()
        await mutability_verbs.execute_delete(
            RestTarget(
                Users,
                "authenticators/{uuid}/users",
                types.rest.CustomMethodMethod.DELETE,
                args=(action.target_uuid,),
                parent=RestTarget(Authenticators, "authenticators"),
            ),
            request,
            authenticator_uuid,
        )
        return f'User "{name}" deleted'

    @typing.override
    async def op_update(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # ALL the ORM work must stay out of the async context: the target
        # resolution, the parent lookup (the authenticator FK) AND the
        # snapshot (the groups m2m needs a query)
        def _build() -> tuple[str, str, JsonObject]:
            user = typing.cast(models.User, self.resolve_target(action.target_uuid))
            # The users PUT is form-shaped (all listed fields required):
            # merge the proposal over the CAS-verified current values.
            params: JsonObject = self.snapshot_values(user, self.etag_fields("user", user))
            params.update(action.values)
            # The membership travels as a uuid list on the wire; normalize
            # csv proposals and keep it internal-authenticators-only
            if isinstance(params.get("groups"), str):
                params["groups"] = _as_uuid_list(params["groups"])
            if not self.membership_editable(user):
                params.pop("groups", None)
            return user.name, user.manager.uuid, params

        name, authenticator_uuid, params = await sync_to_async(_build, thread_sensitive=True)()
        target = RestTarget(
            Users,
            "authenticators/{uuid}/users",
            types.rest.CustomMethodMethod.PUT,
            args=(action.target_uuid,),
            parent=RestTarget(Authenticators, "authenticators"),
        )
        # Detail targets need the parent uuid to resolve (and permission
        # check) the authenticator, so the sync boundary is invoked directly.
        await sync_to_async(RestProxy._execute_sync, thread_sensitive=True)(
            target, request, params, authenticator_uuid
        )
        return f'User "{name}" updated'

    @typing.override
    async def op_custom(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The verb name is the REST custom-method segment (the handler
        # registered it as a POST detail method). FORCE keeps the
        # proposal alive through unrelated drift, so the applicability
        # gate (an MFA still bound) is re-checked here, on the live row.
        verb = action.values.get("action")
        spec = ACTIONS.get(verb) if isinstance(verb, str) else None
        if spec is None:
            raise rest_exceptions.RequestError(
                f"action {verb!r} is not supported (accepted: {', '.join(sorted(ACTIONS))})"
            )

        def _plan() -> tuple[str, str]:
            user = typing.cast(models.User, self.resolve_target(action.target_uuid))
            if not self._verb_applies(spec, user):
                raise rest_exceptions.NotSupportedError(
                    f'The "{spec.name}" action does not apply to user "{user.name}" '
                    "(its authenticator no longer has an MFA bound)"
                )
            return user.name, user.manager.uuid

        name, authenticator_uuid = await sync_to_async(_plan, thread_sensitive=True)()
        await RestProxy().execute(
            RestTarget(
                Users,
                "authenticators/{uuid}/users",
                types.rest.CustomMethodMethod.POST,
                args=(action.target_uuid, spec.name),
                parent=RestTarget(Authenticators, "authenticators"),
            ),
            request,
            {},
            parent_uuid=authenticator_uuid,
        )
        return f'User "{name}" {spec.summary}'
