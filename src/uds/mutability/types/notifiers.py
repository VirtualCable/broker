"""``notifier.update``: propose modifications to an existing notifier.

The gui is the one the REST handler renders for the concrete notifier
type: ``name``, ``comments``, ``tags``, ``level``, ``enabled`` and the
configuration fields of the module. See ``_module_update`` for the
shared details.
"""

from uds import models
from uds.REST.methods.notifiers import Notifiers

from ._module_update import ModuleUpdateActionType


class NotifierUpdate(ModuleUpdateActionType):
    """Proposal: update an existing notifier."""

    type_id = "notifier.update"
    title = "Propose notifier update"
    description = (
        "Propose changes to an existing notifier (system notifications delivery). "
        "The proposal does NOT apply anything: it is queued until an administrator "
        "approves it. Mutable fields are name, comments, tags, level, enabled and "
        "the notifier type configuration fields; use get_mutable_fields to discover "
        "them and their current values."
    )
    handler = Notifiers
    model = models.Notifier
    # The handler lives under the messaging path
    collection = "messaging/notifiers"
    noun = "Notifier"

    # level and enabled are model columns (FIELDS_TO_SAVE); the rest of
    # the gui are configuration fields of the module instance
    model_fields = frozenset({"name", "comments", "tags", "level", "enabled"})
