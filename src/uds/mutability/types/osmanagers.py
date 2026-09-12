"""``osmanager.update``: propose modifications to an existing OS manager.

The gui is the one the REST handler renders for the concrete OS manager
type: ``name``, ``comments``, ``tags`` and the configuration fields of
the module. See ``_module_update`` for the shared details.
"""

from uds import models
from uds.REST.methods.osmanagers import OsManagers

from ._module_update import ModuleUpdateActionType


class OsManagerUpdate(ModuleUpdateActionType):
    """Proposal: update an existing OS manager."""

    type_id = "osmanager.update"
    title = "Propose OS manager update"
    description = (
        "Propose changes to an existing OS manager (how deployed services publish "
        "and manage their operating system). The proposal does NOT apply anything: "
        "it is queued until an administrator approves it. Mutable fields are name, "
        "comments, tags and the OS manager type configuration fields; use "
        "get_mutable_fields to discover them and their current values."
    )
    handler = OsManagers
    model = models.OSManager
    collection = "osmanagers"
    noun = "OS Manager"
