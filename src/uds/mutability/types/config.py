"""``config.update``: propose modifications to UDS global configuration.

Motivation: let an agent in "support mode" propose configuration changes
that fix problems reported by security checks (trusted sources, session
limits, feature gates, ...). The proposal surface is the same as any
other action type: the agent proposes, an administrator approves, and the
execution goes through the canonical REST ``PUT /uds/rest/config``
machinery (validation, audit and permission checks included).

Mutability frontier:

- **Dynamic allowlist**: every configuration value that exists in the
  database (i.e. every declared ``Config.Value`` already materialized) is
  mutable — the set grows as the platform registers new values.
- **Blocklist by field type** (never by name): ``PASSWORD`` and
  ``HIDDEN`` values are neither visible nor mutable. If new secret-ish
  field types appear, they get added to :data:`NOT_MUTABLE_TYPES`.

Proposals are restricted to superusers (see ``check_propose_access``):
configuration has no permission model, and the whole surface is
admin-only anyway (``PUT /config`` requires ``Role.ADMIN``, so a
non-superuser cannot even execute).
"""

import collections.abc
import typing

from django.db import models as db_models

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.core.util.config import Config as CfgConfig
from uds.REST.methods.config import Config as ConfigHandler
from uds.mcp.rest_proxy import RestProxy, RestTarget

from .. import base as mutability_base
from ..etag import item_etag

JsonObject = dict[str, typing.Any]

# Secret-ish configuration is never visible nor mutable (blocklist by
# field type, so new secret types are one line away)
NOT_MUTABLE_TYPES: typing.Final[frozenset[CfgConfig.FieldType]] = frozenset(
    {CfgConfig.FieldType.PASSWORD, CfgConfig.FieldType.HIDDEN}
)

# Config field type -> the gui field type the agent and the admin
# interface already understand
_TYPE_MAP: typing.Final[dict[CfgConfig.FieldType, str]] = {
    CfgConfig.FieldType.TEXT: "text",
    CfgConfig.FieldType.LONGTEXT: "textbox",
    CfgConfig.FieldType.NUMERIC: "numeric",
    CfgConfig.FieldType.BOOLEAN: "checkbox",
    CfgConfig.FieldType.CHOICE: "choice",
    CfgConfig.FieldType.READ: "text",
}


def _split_target(target_uuid: str) -> tuple[str, str]:
    """Split a ``Section.key`` target (the key itself may carry dots)."""
    section, _, key = target_uuid.partition(".")
    return section, key


def _is_mutable(field_type: int) -> bool:
    return CfgConfig.FieldType.from_int(field_type) not in NOT_MUTABLE_TYPES


class ConfigUpdate(mutability_base.MutableActionType):
    """Proposal: update one global configuration value.

    The target is the ``Section.key`` string (the key may carry dots).
    One action mutates exactly one value; the CAS base is the current
    value, so any concurrent change to it invalidates the proposal.
    """

    type_id = "config.update"
    title = "Propose configuration change"
    description = (
        "Propose a change to one UDS global configuration value. The proposal does NOT "
        "apply anything: it is queued until an administrator approves it. Secret values "
        "(passwords, hidden) are neither visible nor mutable; use get_mutable_fields "
        "with for_type set to the section name (Security, UDS, Custom, ...) to discover "
        "the mutable keys, their types and current values."
    )
    handler = ConfigHandler

    # ------------------------------------------------- generic CAS layer

    @typing.override
    def resolve_target(self, target_uuid: str) -> db_models.Model:
        section, key = _split_target(target_uuid)
        try:
            return models.Config.objects.get(section=section, key=key)
        except models.Config.DoesNotExist:
            raise rest_exceptions.NotFound(f"Configuration value {target_uuid} not found") from None

    @typing.override
    def target_uuid_of(self, target: db_models.Model) -> str:
        cfg = typing.cast(models.Config, target)
        return f"{cfg.section}.{cfg.key}"

    @typing.override
    def check_propose_access(self, user: "models.User", target: db_models.Model) -> None:
        # Configuration has no permission model: the proposal surface is
        # superuser only (execution is admin-gated by the handler itself)
        if not user.is_admin:
            raise rest_exceptions.AccessDenied()

    @typing.override
    def for_type_of(self, target: db_models.Model) -> str:
        return typing.cast(models.Config, target).section

    @typing.override
    def field_definitions(self, for_type: str, target: db_models.Model | None = None) -> list[JsonObject]:
        """Mutable keys of one section (secret types excluded).

        Same source the admin configuration panel uses
        (``Config.enumerate``): every declared, materialized value of the
        section, minus the secret-ish field types.
        """
        defs: list[JsonObject] = []
        for value in CfgConfig.enumerate():
            if value.section() != for_type or not _is_mutable(value.get_type()):
                continue
            field_type = CfgConfig.FieldType.from_int(value.get_type())
            definition: JsonObject = {
                "name": f"{value.section()}.{value.key()}",
                "type": _TYPE_MAP.get(field_type, "text"),
                "label": value.key(),
                "tooltip": value.get_help(),
                "secret": False,
                "current": value.get(),
            }
            if field_type == CfgConfig.FieldType.BOOLEAN:
                definition["choices"] = ["true", "false"]
            defs.append(definition)
        return defs

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        cfg = typing.cast(models.Config, target)
        name = f"{cfg.section}.{cfg.key}"
        return {name: cfg.value} if name in set(names) else {}

    @typing.override
    def etag_fields(self, for_type: str, target: db_models.Model | None = None) -> list[str]:
        # The "whole item" is the single key: only concurrent changes to
        # the very value being proposed invalidate the CAS
        if target is None:
            return []
        cfg = typing.cast(models.Config, target)
        return [f"{cfg.section}.{cfg.key}"]

    @typing.override
    def fingerprint(self, target: db_models.Model) -> str:
        cfg = typing.cast(models.Config, target)
        # CAS fingerprints are only ever compared to themselves; the
        # value is keyed as "value" because etag field names cannot
        # carry dots (the Section.key target does)
        return item_etag({"value": cfg.value}, ("value",))

    # ------------------------------------------------------------- hooks

    @typing.override
    async def execute(self, action: "models.FlowAction", request: ExtendedHttpRequestWithUser) -> str:
        # Same shape the REST PUT accepts: {section: {key: {value}}}.
        # DB-free: a JSON bool is unambiguous, and it is normalized
        # exactly like ``Config.Value.set`` does (bool -> "0"/"1")
        name, value = next(iter(action.values.items()))
        if isinstance(value, bool):
            value = "1" if value else "0"
        section, key = _split_target(name)
        await RestProxy().execute(
            RestTarget(ConfigHandler, "config", types.rest.CustomMethodMethod.PUT),
            request,
            {section: {key: {"value": str(value)}}},
        )
        return f"Configuration value {name} updated"
