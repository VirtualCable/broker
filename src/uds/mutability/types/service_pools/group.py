"""``servicepool.group``: set the access groups of a service pool.

M2M relation family: ``groups`` is the complete desired set (option A),
never a delta. Execution attaches/detaches through the canonical
``services_pools/{uuid}/groups`` detail surface.
"""

import typing

from uds import models
from uds.REST.methods.services_pools import ServicesPools
from uds.REST.methods.user_services import Groups as AssignedGroups

from .._relations import M2MRelationActionType

JsonObject = dict[str, typing.Any]


class ServicePoolGroup(M2MRelationActionType):
    """Proposal: set the complete desired access groups of a service pool."""

    type_id = "servicepool.group"
    relation_label = "access groups"
    uuid_source = "get_servicepool_group (or the group list tools)"
    title = "Propose service pool groups"
    description = (
        "Propose the complete desired set of groups allowed to use a service pool. "
        "The 'groups' field is the FINAL set, not a delta: groups missing from it "
        "lose access, new ones gain it. Group uuids come from the groups list tools "
        "(authenticator groups are also assignable). An empty list removes access "
        "for every group. The proposal does NOT apply anything: it is queued until "
        "an administrator approves it."
    )
    handler = ServicesPools
    model = models.ServicePool
    noun = "Service pool"
    subtype = "servicepool"

    field_name = "groups"
    field_label = "Allowed groups"
    field_tooltip = (
        "Complete desired set of group uuids allowed to use this service pool. Pairs "
        "are validated against existing groups; discover the uuids with the groups tools."
    )
    item_label = "group"
    related_model = models.Group
    include_choices = False
    relation_manager = "assignedGroups"
    detail_handler = AssignedGroups
    detail_path = "services_pools/{uuid}/groups"
    parent_collection = "services_pools"
