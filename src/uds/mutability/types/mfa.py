"""``mfa.update``: propose modifications to an existing MFA provider.

The gui is the one the REST handler renders for the concrete MFA type:
``name``, ``comments``, ``tags``, ``remember_device``, ``validity`` and
the configuration fields of the module. See ``_module_update`` for the
shared details.
"""

from uds import models
from uds.REST.methods.mfas import MFA

from ._module_update import ModuleUpdateActionType


class MFAUpdate(ModuleUpdateActionType):
    """Proposal: update an existing MFA provider."""

    type_id = "mfa.update"
    title = "Propose MFA provider update"
    description = (
        "Propose changes to an existing MFA provider (multi factor authentication "
        "configuration). The proposal does NOT apply anything: it is queued until an "
        "administrator approves it. Mutable fields are name, comments, tags, "
        "remember_device, validity and the MFA type configuration fields; use "
        "get_mutable_fields to discover them and their current values."
    )
    handler = MFA
    model = models.MFA
    collection = "mfa"
    noun = "MFA provider"

    # remember_device and validity are model columns (FIELDS_TO_SAVE);
    # the rest of the gui are configuration fields of the module instance
    model_fields = frozenset({"name", "comments", "tags", "remember_device", "validity"})
