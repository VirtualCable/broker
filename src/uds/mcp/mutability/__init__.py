"""Supervised mutability for MCP tools.

The AI proposes mutations; administrators approve them. This package
holds the shared machinery (:mod:`.actions` lifecycle, :mod:`.storage`
persistence, :mod:`.etag` fingerprints). The action type registry, the
MCP tools and the admin REST endpoints are built on top of these.
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
from .storage import STORAGE_OWNER, PendingActionStore

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
    "item_etag",
]
