"""``servicepool.transport``: set the transports of a service pool.

M2M relation family: ``transports`` is the complete desired set (option
A), never a delta. Execution attaches/detaches through the canonical
``services_pools/{uuid}/transports`` detail surface.
"""

import typing

from uds import models
from uds.REST.methods.services_pools import ServicesPools
from uds.REST.methods.user_services import Transports

from .._relations import M2MRelationActionType

JsonObject = dict[str, typing.Any]


class ServicePoolTransport(M2MRelationActionType):
    """Proposal: set the complete desired transports of a service pool."""

    type_id = "servicepool.transport"
    relation_label = "transports"
    uuid_source = "get_servicepool_transport (or the transport list tools)"
    title = "Propose service pool transports"
    description = (
        "Propose the complete desired set of transports of a service pool (the "
        "connections users may establish). The 'transports' field is the FINAL set, "
        "not a delta: transports missing from it are detached, new ones are "
        "attached. Use get_mutable_fields with the pool uuid to see the current "
        "transports and the uuids available. An empty list detaches every "
        "transport. The proposal does NOT apply anything: it is queued until an "
        "administrator approves it."
    )
    handler = ServicesPools
    model = models.ServicePool
    noun = "Service pool"
    subtype = "servicepool"

    field_name = "transports"
    field_label = "Transports"
    field_tooltip = (
        "Complete desired set of transport uuids attached to this service pool. "
        "Transports are pool-independent objects; this only attaches and detaches them."
    )
    item_label = "transport"
    related_model = models.Transport
    include_choices = True
    relation_manager = "transports"
    detail_handler = Transports
    detail_path = "services_pools/{uuid}/transports"
    parent_collection = "services_pools"
