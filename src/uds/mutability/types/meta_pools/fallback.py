"""``metapool.fallback``: change the fallback access policy of a meta pool.

The fallback is the decision taken when no calendar rule matches (the
access calendars rules only say ALLOW/DENY *when* their calendar is
active). It is a plain scalar on the meta pool, written through the
canonical ``meta_pools/{uuid}/fallback_access`` custom method (POST),
and it is deliberately its own family: the ``metapool.update`` PUT does
not accept it and the access-rules set does not touch it, so the CAS
stays per-concern. There is no ``read`` override: the current value is
published by the plain ``GET meta_pools/{uuid}/fallback_access`` and the
metapool read tools.
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
from uds.REST.methods.meta_pools import MetaPools

from ... import base as mutability_base
from ...etag import item_etag

JsonObject = dict[str, typing.Any]

_ALLOWED_ACCESS_VALUES: typing.Final[frozenset[str]] = frozenset(
    {types.states.State.ALLOW, types.states.State.DENY}
)


class MetaPoolFallback(mutability_base.MutableActionType):
    """Proposal: change the fallback access policy of a meta pool."""

    type_id = "metapool.fallback"

    title = "Propose meta pool fallback access"
    description = (
        "Propose the fallback access policy of a meta pool: what happens when no "
        "access calendar rule applies (ALLOW lets everyone in, DENY keeps everyone "
        "out, unless a calendar rule says otherwise). The proposal does NOT apply "
        "anything: it is queued until an administrator approves it."
    )
    handler = MetaPools
    model = models.MetaPool
    noun = "Meta pool"

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        try:
            return models.MetaPool.objects.get(uuid__iexact=target_uuid)
        except models.MetaPool.DoesNotExist:
            raise rest_exceptions.NotFound("Meta pool not found") from None

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return "metapool"

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        return [
            {
                "name": "fallback",
                "type": types.ui.FieldType.CHOICE.value,
                "label": "Fallback access",
                "tooltip": "Access policy applied when no calendar rule matches: ALLOW or DENY.",
                "secret": False,
                "choices": [{"value": "ALLOW", "label": "Allowed"}, {"value": "DENY", "label": "Denied"}],
            }
        ]

    @typing.override
    def validate_values(
        self,
        for_type: str,
        values: JsonObject,
        target: db_models.Model | None = None,
    ) -> list[str]:
        errors = super().validate_values(for_type, values, target)
        flat = self.flatten_values(values)
        if "fallback" not in flat:
            errors.append("field fallback is required (ALLOW or DENY)")
        elif flat["fallback"] not in _ALLOWED_ACCESS_VALUES:
            errors.append("field fallback must be ALLOW or DENY")
        return errors

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        return ["fallback"]

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        meta_pool = typing.cast(models.MetaPool, target)
        wanted = set(names)
        return {"fallback": meta_pool.fallbackAccess} if "fallback" in wanted else {}

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        meta_pool = typing.cast(models.MetaPool, target)
        return item_etag({"fallback": meta_pool.fallbackAccess}, ["fallback"])

    @typing.override
    def target_display_name(self, target: db_models.Model) -> str:
        return typing.cast(models.MetaPool, target).name

    # ---------------------------------------------------------- execution

    @typing.override
    async def op_update(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        def _meta_pool_name() -> str:
            meta_pool = typing.cast(models.MetaPool, self.resolve_target(action.target_uuid))
            return meta_pool.name

        meta_pool_name = await sync_to_async(_meta_pool_name, thread_sensitive=True)()
        fallback = typing.cast(str, action.values["fallback"])
        await RestProxy().execute(
            RestTarget(
                MetaPools,
                "meta_pools",
                types.rest.CustomMethodMethod.POST,
                args=(action.target_uuid, "fallback_access"),
            ),
            request,
            {"fallback_access": fallback},
        )
        return f'Meta pool "{meta_pool_name}" fallback access set to {fallback}'
