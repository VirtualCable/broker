"""``user.update``: propose modifications to an existing user.

Second level resource (authenticators detail): the proposal only needs
the user uuid — the parent authenticator is derived from the ``manager``
FK at execution time (an user never changes authenticator), exactly like
``calendar_rule.update`` does for rules. Permissions are MANAGEMENT over
the authenticator, inherited through :meth:`permission_target`.

The users PUT is form-shaped and requires ``name``, ``real_name``,
``comments``, ``state``, ``staff_member`` and ``is_admin``; ``password``
and ``mfa_data`` are optional (absent on the PUT = keep current).

``staff_member`` and ``is_admin`` are privilege escalation vectors, so
they are NOT proposable: they travel as immutable context in every
payload (the PUT requires them) but the proposal cannot touch them.

The ``groups`` relation (m2m) and the ``token``/``last_access``/``parent``
columns are NOT part of the mutable surface either.

``password`` is write-only: never snapshotted (CAS cannot observe it),
never sent unless the proposal carries it. The REST handler hashes it.
"""

import collections.abc
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

from .. import base as mutability_base
from ..etag import item_etag

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
]


class UserUpdate(mutability_base.MutableActionType):
    """Proposal: update an existing user."""

    type_id = "user.update"
    title = "Propose user update"
    description = (
        "Propose changes to an existing user of an authenticator. The proposal does "
        "NOT apply anything: it is queued until an administrator approves it. Mutable "
        "fields are name (username), real_name, comments, state, mfa_data and "
        "password (write-only: only sent when proposed, and hashed by the broker). "
        "The staff/admin privileges cannot be granted through proposals and group "
        "membership is not part of this proposal."
    )
    model = models.User
    noun = "User"

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

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
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
            {
                "name": "password",
                "type": "password",
                "label": "Password",
                "tooltip": "Write-only: omit to keep the current password (hashed on apply)",
                "secret": True,
            },
        ]

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
        return snapshot

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        return list(_USER_FIELDS)

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        fields = self.etag_fields("user")
        item_dict: JsonObject = self.snapshot_values(target, fields)
        return item_etag(item_dict, fields)

    # ---------------------------------------------------------- execution

    @typing.override
    async def execute(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # ORM work must stay out of the async context, including the
        # parent lookup (the authenticator FK needs a query)
        def _resolve() -> tuple[models.User, str]:
            user = typing.cast(models.User, self.resolve_target(action.target_uuid))
            return user, user.manager.uuid

        user, authenticator_uuid = await sync_to_async(_resolve, thread_sensitive=True)()
        # The users PUT is form-shaped (all listed fields required):
        # merge the proposal over the CAS-verified current values.
        params: JsonObject = self.snapshot_values(user, self.etag_fields("user"))
        params.update(action.values)
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
        return f'User "{user.name}" updated'
