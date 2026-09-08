"""Supervised mutability for MCP tools.

The AI proposes mutations; administrators approve them. This package
holds the shared machinery (:mod:`.actions` lifecycle, :mod:`.storage`
persistence, :mod:`.etag` fingerprints), the action type
:mod:`.registry` and the MCP :mod:`.tools` (proposals, discovery and
self-management). The admin approval endpoints are built on top.
"""

from .actions import (
    MAX_PENDING_PER_USER,
    PENDING_TTL,
    ActionStatus,
    InvalidTransition,
    MutabilityError,
    NotActionOwner,
    PendingAction,
)
from .etag import item_etag
from .registry import all_types, get, register
from .storage import STORAGE_OWNER, PendingActionStore
from .tools import register_mutability_tools

__all__ = [
    "MAX_PENDING_PER_USER",
    "PENDING_TTL",
    "STORAGE_OWNER",
    "ActionStatus",
    "InvalidTransition",
    "MutabilityError",
    "NotActionOwner",
    "PendingAction",
    "PendingActionStore",
    "all_types",
    "get",
    "item_etag",
    "register",
    "register_mutability_tools",
]
