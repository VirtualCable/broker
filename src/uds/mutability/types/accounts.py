"""``account.update``: propose modifications to an existing account
(the accounting containers attached to providers' usage).

Replicates the REST ``PUT /accounts/{uuid}``: ``name``, ``comments`` and
``tags``. Validation, serialization and execution reuse the very same
handler machinery.
"""

import collections.abc
import typing
from types import SimpleNamespace as _Namespace

from asgiref.sync import sync_to_async
from django.db import models as db_models

from uds import models
from uds.core import types
from uds.REST.methods.accounts import Accounts
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.mcp.rest_proxy import RestProxy, RestTarget

from .. import base as mutability_base
from .. import gui_view
from ..etag import item_etag

JsonObject = dict[str, typing.Any]


class AccountUpdate(mutability_base.MutableActionType):
    """Proposal: update an existing account."""

    type_id = "account"
    title = "Propose account update"
    description = (
        "Propose changes to an existing account (the accounting container of services "
        "usage). The proposal does NOT apply anything: it is queued until an "
        "administrator approves it. Mutable fields are name, comments and tags; use "
        "get_mutable_fields to discover them and their current values."
    )
    handler = Accounts

    # ------------------------------------------------------------- hooks

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.Account.objects.get(uuid__iexact=target_uuid)
        except models.Account.DoesNotExist:
            raise rest_exceptions.NotFound("Account not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return "account"

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        shim = typing.cast(typing.Any, _Namespace())
        # The gui fields are exactly the handler's own FIELDS_TO_SAVE
        # columns; the agent view derives from the handler gui, so any
        # change there propagates here automatically
        columns = frozenset(name for name, _modifier in Accounts.parse_save_fields(Accounts.FIELDS_TO_SAVE))
        return gui_view.agent_definitions(
            sorted(Accounts.get_gui(shim, for_type), key=lambda element: element.gui.order),
            from_instance=lambda name: name not in columns,
        )

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        account = typing.cast(models.Account, target)
        wanted = set(names)
        snapshot: JsonObject = {}
        for name in wanted:
            if name == "name":
                snapshot[name] = account.name
            elif name == "comments":
                snapshot[name] = account.comments
            elif name == "tags":
                snapshot[name] = sorted(t.tag for t in account.tags.all())
        return snapshot

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        # The merged PUT carries exactly the proposable surface (no context
        # fields needed), so fingerprint == definitions
        return [d["name"] for d in self.field_definitions(for_type)]

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        fields = self.etag_fields("account")
        item_dict: JsonObject = self.snapshot_values(target, fields)
        return item_etag(item_dict, fields)

    @typing.override
    async def op_update(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # The PUT is form-shaped (every FIELDS_TO_SAVE entry is required):
        # merge the proposal over the CAS-verified current values, so a
        # partial proposal applies instead of failing on approval.
        # ALL the ORM work must stay out of the async context.
        def _build_params() -> tuple[str, JsonObject]:
            account = typing.cast(models.Account, self.resolve_target(action.target_uuid))
            params = self.snapshot_values(account, self.etag_fields("account"))
            params.update(action.values)
            return account.name, params

        account_name, params = await sync_to_async(_build_params, thread_sensitive=True)()
        await RestProxy().execute(
            RestTarget(
                Accounts,
                "accounts",
                types.rest.CustomMethodMethod.PUT,
                args=(action.target_uuid,),
            ),
            request,
            params,
        )
        return f'Account "{account_name}" updated'
