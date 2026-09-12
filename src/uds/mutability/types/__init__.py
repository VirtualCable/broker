"""
Concrete mutable action types of UDS, one module per domain
(``providers``, ``services``, ``servers``, ``config``, ...).

The package itself is empty on purpose: the registry
(:mod:`uds.mutability.registry`) imports it and auto-loads every module
placed here (modfinder style). Any
:class:`uds.mutability.base.MutableActionType` subclass carrying its own
``type_id`` self-registers. To add a new domain, drop a module in this
folder — there are no imports to wire anywhere.
"""
