"""``transport.update``: propose modifications to an existing transport.

Replicates the REST ``PUT /transports/{uuid}`` for a concrete transport
type: ``name``, ``comments``, ``tags``, ``priority``, ``label``,
``net_filtering``, ``allowed_oss`` and the configuration fields of the
transport module. The ``networks`` and ``pools`` relations (m2m) are NOT
part of the mutable surface; when the proposal does not carry them, the
PUT leaves them untouched (the handler's ``post_save`` skips absent
params).

Validation, serialization and execution reuse the very same handler
machinery (see ``_module_update`` for the shared details).
"""

import collections.abc
import typing

from uds import models
from uds.REST.methods.transports import Transports

from ._module_update import ModuleUpdateActionType


class TransportUpdate(ModuleUpdateActionType):
    """Proposal: update an existing transport."""

    type_id = "transport.update"
    title = "Propose transport update"
    description = (
        "Propose changes to an existing transport (how users access their services). "
        "The proposal does NOT apply anything: it is queued until an administrator "
        "approves it. Mutable fields are name, comments, tags, priority, label, "
        "net_filtering, allowed_oss and the transport type configuration fields; use "
        "get_mutable_fields to discover them and their current values. The networks "
        "and service pools relations are not part of this proposal."
    )
    handler = Transports
    model = models.Transport
    collection = "transports"
    noun = "Transport"

    # Columns the REST PUT reads from params (FIELDS_TO_SAVE); the rest
    # of the gui are configuration fields of the transport instance
    model_fields = frozenset({"name", "comments", "tags", "priority", "net_filtering", "allowed_oss", "label"})
    # m2m relations are not mutable: absent on the PUT, untouched by post_save
    excluded = frozenset({"networks", "pools"})
    # The column stores a CSV of allowed OS ids; the PUT expects a list
    snapshot_adapters: typing.ClassVar[dict[str, collections.abc.Callable[[typing.Any], typing.Any]]] = {
        "allowed_oss": lambda value: [x for x in value.split(",")] if value else [],
    }
