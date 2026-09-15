"""``authenticator.update``: propose modifications to an authenticator.

The gui is the one the REST handler renders for the concrete
authenticator type: ``name``, ``comments``, ``tags``, ``priority``,
``small_name``, ``state``, ``net_filtering`` and the configuration
fields of the module. The ``networks`` relation (m2m) and the ``mfa_id``
reference are NOT part of the mutable surface.

The authenticator PUT is form-shaped, so execution merges the proposal
over the CAS-verified current values (see ``_module_update``).

The users and groups of an authenticator live in sibling modules (their
own action types, ``user.update`` and ``group.update``); the imports at
the bottom of this module pull them in so the registry discovers every
family of the package.
"""

from uds import models
from uds.REST.methods.authenticators import Authenticators

from .._module_update import ModuleUpdateActionType

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
        "access (visible/hidden/disabled), network filtering mode and the "
        "authenticator type configuration fields; use get_mutable_fields to "
        "discover them and their current values. The networks relation and the "
        "assigned MFA are not part of this proposal."
    )
    handler = Authenticators
    model = models.Authenticator
    collection = "authenticators"
    noun = "Authenticator"
