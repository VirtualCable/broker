"""``authenticator.update``: propose modifications to an authenticator.

The gui is the one the REST handler renders for the concrete
authenticator type: ``name``, ``comments``, ``tags``, ``priority``,
``small_name``, ``state``, ``net_filtering`` and the configuration
fields of the module. The ``networks`` relation (m2m) and the ``mfa_id``
reference are NOT part of the mutable surface.

The authenticator PUT is form-shaped, so execution merges the proposal
over the CAS-verified current values (see ``_module_update``).
"""

from uds import models
from uds.REST.methods.authenticators import Authenticators

from ._module_update import ModuleUpdateActionType


class AuthenticatorUpdate(ModuleUpdateActionType):
    """Proposal: update an existing authenticator."""

    type_id = "authenticator.update"
    title = "Propose authenticator update"
    description = (
        "Propose changes to an existing authenticator (how users log in). The "
        "proposal does NOT apply anything: it is queued until an administrator "
        "approves it. Mutable fields are name, comments, tags, priority, label, "
        "access (visible/hidden/disabled), network filtering mode and the "
        "authenticator type configuration fields; use get_mutable_fields to "
        "discover them and their current values. The networks relation and the "
        "assigned MFA are not part of this proposal."
    )
    handler = Authenticators
    model = models.Authenticator
    collection = "authenticators"
    noun = "Authenticator"

    # Columns the REST PUT reads from params (FIELDS_TO_SAVE minus the
    # optional mfa_id FK reference)
    model_fields = frozenset({"name", "comments", "tags", "priority", "small_name", "state", "net_filtering"})
    # networks is m2m; mfa_id is a FK reference to another resource
    excluded = frozenset({"networks", "mfa_id"})
