"""``transport.update`` / ``transport.create`` / ``transport.delete``.

Replicates the REST ``PUT``/``POST``/``DELETE`` on ``/transports`` for a
concrete transport type: ``name``, ``comments``, ``tags``, ``priority``,
``label``, ``net_filtering``, ``allowed_oss``, the configuration fields of
the transport module, and the ``networks`` and ``pools`` relations (m2m,
riding post_save exactly like the administration form sends them). As in
the authenticator family, the relations travel as uuid lists and are part
of the CAS snapshot: a proposal that omits them keeps them untouched (the
merged PUT carries their current values) and any drift revokes the
action.

Validation, serialization and execution reuse the very same handler
machinery (see ``_module_update`` for the shared details).
"""

import collections.abc
import typing

from django.db import models as db_models

from uds import models
from uds.REST.methods.transports import Transports

from ._module_update import ModuleUpdateActionType

JsonObject = dict[str, typing.Any]


class TransportUpdate(ModuleUpdateActionType):
    """Proposal: update an existing transport."""

    type_id = "transport"
    title = "Propose transport update"
    description = (
        "Propose changes to an existing transport (how users access their services). "
        "The proposal does NOT apply anything: it is queued until an administrator "
        "approves it. Mutable fields are name, comments, tags, priority, label, "
        "net_filtering, allowed_oss, the networks and service pools relations, and "
        "the transport type configuration fields; use get_mutable_fields to discover "
        "them and their current values."
    )
    handler = Transports
    model = models.Transport
    collection = "transports"
    noun = "Transport"

    # The column stores a CSV of allowed OS ids; the PUT expects a list
    snapshot_adapters: typing.ClassVar[dict[str, collections.abc.Callable[[typing.Any], typing.Any]]] = {
        "allowed_oss": lambda value: [x for x in value.split(",")] if value else [],
    }

    # networks and pools are m2m relations riding post_save: they travel
    # in the params next to the columns (never inside "instance"), and on
    # creation they are always present (omitted ones ride as the empty
    # list the gui sends, what keeps the handler's post_save from
    # coupling one relation's processing to the presence of the other)
    extra_params_fields = frozenset({"networks", "pools"})

    # The two required columns whose gui fields declare no default (the
    # admin form always submits them): an omitted one rides these values
    create_column_defaults: typing.ClassVar[dict[str, typing.Any]] = {
        "allowed_oss": [],
        "label": "",
    }

    @typing.override
    def snapshot_values(self, target: db_models.Model, names: collections.abc.Iterable[str]) -> JsonObject:
        # The two relations need their own resolution: both are m2m
        # (sorted uuid lists, the shape the PUT sends back; sorted so the
        # fingerprint does not depend on the row order). Everything else
        # resolves through the generic snapshot.
        transport = typing.cast(models.Transport, target)
        snapshot: JsonObject = {}
        generic: list[str] = []
        for name in names:
            if name == "networks":
                snapshot[name] = sorted(n.uuid for n in transport.networks.all())
            elif name == "pools":
                snapshot[name] = sorted(p.uuid for p in transport.deployedServices.all())
            else:
                generic.append(name)
        snapshot.update(super().snapshot_values(target, generic))
        return snapshot
