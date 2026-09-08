"""Storage-backed persistence for pending mutability actions.

Uses the generic :class:`uds.core.util.storage.Storage` with a private
owner (no new table, no migration). The free ``attr1`` column carries
the action status, so the hot query (the admin pending queue) is a SQL
filter; the expected volume is small either way.
"""

import base64
import datetime
import typing

from uds.core.util.storage import Storage
from uds.models.storage import Storage as DBStorage

from .actions import ActionStatus, PENDING_TTL, PendingAction

STORAGE_OWNER: typing.Final[str] = "mcp-mutability"


class PendingActionStore:
    """Load/save/list :class:`PendingAction` on the generic Storage."""

    def __init__(self) -> None:
        self._storage = Storage(STORAGE_OWNER)

    @staticmethod
    def _key(action_id: str) -> str:
        return f"act_{action_id}"

    def save(self, action: PendingAction) -> None:
        """Upsert the action, indexing its current status on attr1.

        Uses ``update_or_create`` (same pattern as ``StorageAsDict``)
        instead of ``Storage.save_to_db``, whose upsert relies on
        swallowing the create's UNIQUE failure — that breaks the
        transaction under sqlite/test contexts when re-saving a key.
        """
        DBStorage.objects.update_or_create(
            key=self._storage.get_key(self._key(action.id)),
            defaults={
                "owner": STORAGE_OWNER,
                "data": base64.b64encode(action.to_json().encode("utf-8")).decode(),
                "attr1": action.status.value,
            },
        )

    def get(self, action_id: str) -> PendingAction | None:
        """Return the action with this id, or None if it does not exist."""
        raw = self._storage.read_string(self._key(action_id))
        if raw is None:
            return None
        action = PendingAction.from_json(raw)
        # Lazy expiry: a pending action past its TTL is decided here
        self._maybe_expire(action)
        return action

    def list(
        self,
        *,
        status: ActionStatus | None = None,
        owner_uuid: str | None = None,
    ) -> list[PendingAction]:
        """List actions, optionally filtered by status and/or owner.

        Pending actions past their TTL are expired (and persisted as
        such) during the scan; they are skipped here and will show up
        under ``EXPIRED`` on subsequent listings.
        """
        result: list[PendingAction] = []
        for _key, data, _attr1 in self._storage.filter(attr1=status.value if status else None):
            action = PendingAction.from_json(data.decode("utf-8"))
            if owner_uuid is not None and action.owner_uuid != owner_uuid:
                continue
            if self._maybe_expire(action):
                continue
            result.append(action)
        return result

    def count_pending(self, *, owner_uuid: str) -> int:
        """Number of still-pending proposals of one user (spam cap)."""
        return len(self.list(status=ActionStatus.PENDING, owner_uuid=owner_uuid))

    def delete(self, action_id: str) -> None:
        """Remove the action record entirely."""
        self._storage.remove(self._key(action_id))

    def _maybe_expire(self, action: PendingAction) -> bool:
        """Expire (and persist) the action if its pending TTL is gone."""
        if action.status != ActionStatus.PENDING:
            return False
        age = datetime.datetime.now(datetime.UTC) - action.created
        if age <= PENDING_TTL:
            return False
        action.expire()
        self.save(action)
        return True
