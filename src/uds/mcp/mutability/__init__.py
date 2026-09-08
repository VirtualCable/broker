"""Supervised mutability for MCP tools.

The AI proposes mutations; administrators approve them. This package
holds the shared machinery (the :class:`.store.FlowStore` application
layer over the ``ActionFlow``/``FlowAction`` models, and the
:mod:`.etag` fingerprints), the action type :mod:`.registry` and the
MCP :mod:`.tools` (proposals, discovery and self-management). The admin
approval endpoints are built on top.
"""

from .etag import item_etag
from .registry import all_types, get, register
from .store import FlowStore, InvalidTransition, MutabilityError, NotActionOwner
from .tools import register_mutability_tools

__all__ = [
    "FlowStore",
    "InvalidTransition",
    "MutabilityError",
    "NotActionOwner",
    "all_types",
    "get",
    "item_etag",
    "register",
    "register_mutability_tools",
]
