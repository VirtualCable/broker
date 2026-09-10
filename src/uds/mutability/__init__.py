"""Supervised mutability for UDS.

The AI proposes mutations; administrators approve them. This package
holds the shared machinery (the :class:`.store.FlowStore` application
layer over the ``ActionFlow``/``FlowAction`` models, and the
:mod:`.etag` fingerprints), the action type :mod:`.registry` and the
concrete adapters (:mod:`.types_providers`, :mod:`.types_services`).
The MCP tools live in
:mod:`uds.mcp.mutability` (proposals, discovery and self-management),
and the REST owner/management surfaces are built on top.
"""

from .base import StalePolicy
from .etag import item_etag
from .registry import all_types, get, register
from .store import FlowStore, InvalidTransition, MutabilityError, NotActionOwner, StaleProposal

__all__ = [
    "FlowStore",
    "InvalidTransition",
    "MutabilityError",
    "NotActionOwner",
    "StalePolicy",
    "StaleProposal",
    "all_types",
    "get",
    "item_etag",
    "register",
]
