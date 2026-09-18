"""``authenticator.update``: propose modifications to an authenticator.

The gui is the one the REST handler renders for the concrete
authenticator type: ``name``, ``comments``, ``tags``, ``priority``,
``small_name``, ``state``, ``net_filtering``, the ``networks`` relation
(m2m, riding post_save), the ``mfa_id`` reference (FK with SET_NULL)
and the configuration fields of the module.

The authenticator PUT/POST is form-shaped, so execution merges the
proposal over the CAS-verified current values (see ``_module_update``);
the m2m and FK relations are part of that snapshot, so a proposal that
omits them keeps them untouched and any drift revokes the action.

The users and groups of an authenticator live in sibling modules (their
own action types, ``user.update`` and ``group.update``); the imports at
the bottom of this module pull them in so the registry discovers every
family of the package.
"""

import collections.abc
import typing

from django.db import models as db_models

from uds import models
from uds.REST.methods.authenticators import Authenticators

from .._module_update import ModuleUpdateActionType

JsonObject = dict[str, typing.Any]

from . import group as group
from . import user as user


class AuthenticatorUpdate(ModuleUpdateActionType):
    """Proposal: update an existing authenticator."""

    type_id = "authenticator"
    title = "Propose authenticator update"
    description = (
        "Propose changes to an existing authenticator (how users log in). The "
        "proposal does NOT apply anything: it is queued until an administrator "
        "approves it. Mutable fields are name, comments, tags, priority, label, "
        "access (visible/hidden/disabled), network filtering mode, the allowed "
        "networks, the MFA provider (empty string clears it) and the "
        "authenticator type configuration fields; use get_mutable_fields to "
        "discover them and their current values."
    )
    handler = Authenticators
    model = models.Authenticator
    collection = "authenticators"
    noun = "Authenticator"

    # networks travels in the POST params (post_save reads it there); it
    # is not a model column, so it must not fall into "instance"
    extra_params_fields = frozenset({"networks"})

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        # The two relations need their own resolution: networks is a m2m
        # (uuid list, the shape the PUT sends back) and mfa_id is stored
        # as an int FK id but travels as an uuid (or empty string) on the
        # wire. Everything else resolves through the generic snapshot.
        auth = typing.cast(models.Authenticator, target)
        snapshot: JsonObject = {}
        generic: list[str] = []
        for name in names:
            if name == "networks":
                snapshot[name] = [n.uuid for n in auth.networks.all()]
            elif name == "mfa_id":
                snapshot[name] = auth.mfa.uuid if auth.mfa else ""
            else:
                generic.append(name)
        snapshot.update(super().snapshot_values(target, generic))
        return snapshot
