"""Pending action model and lifecycle for the supervised mutability.

The AI never applies changes directly: it proposes them, and every
proposal is stored as a :class:`PendingAction` that an administrator
has to approve from the administration interface.

Design (doc/plan/mcp-mutability.md):

- Proposals are conditional swaps (CAS): the server snapshots the
  current values of the touched fields (``base_values``) and the whole
  item fingerprint (``base_etag``) at proposal time.
- Decided states are terminal. Nobody re-opens a decided action.
"""

import dataclasses
import datetime
import enum
import json
import typing
import uuid

MAX_PENDING_PER_USER: typing.Final[int] = 20
"""Maximum not-yet-decided proposals a single user may have open."""

PENDING_TTL: typing.Final[datetime.timedelta] = datetime.timedelta(days=7)
"""How long a pending proposal survives before it is expired."""


class MutabilityError(ValueError):
    """Base error for the mutability subsystem."""


class InvalidTransition(MutabilityError):
    """A pending action cannot move to the requested state."""


class NotActionOwner(MutabilityError):
    """The actor is not the owner of the pending action."""


@enum.unique
class ActionStatus(enum.StrEnum):
    """Lifecycle states of a :class:`PendingAction`."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self != ActionStatus.PENDING


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@dataclasses.dataclass
class PendingAction:
    """A proposed mutation, awaiting administrator approval."""

    id: str
    type_id: str  # key in the mutability registry (e.g. "provider.update")
    owner_uuid: str  # UDS user owning the agent token (proposer)
    agent: str  # agent clientInfo (name/version), informational
    target_uuid: str  # uuid of the item to be mutated ('' on creates)
    values: dict[str, typing.Any]  # proposed payload (data, never code)
    base_values: dict[str, typing.Any]  # server-side snapshot of the touched fields at proposal time
    base_etag: str  # fingerprint of the whole item at proposal time
    created: datetime.datetime
    updated: datetime.datetime
    status: ActionStatus = ActionStatus.PENDING
    decided_by: str | None = None
    decided_at: datetime.datetime | None = None
    result: str | None = None

    @staticmethod
    def create(
        *,
        type_id: str,
        owner_uuid: str,
        agent: str,
        target_uuid: str,
        values: dict[str, typing.Any],
        base_values: dict[str, typing.Any],
        base_etag: str,
    ) -> "PendingAction":
        """Build a fresh, pending proposal with server-side identity and snapshots."""
        now = _now()
        return PendingAction(
            id=str(uuid.uuid4()),
            type_id=type_id,
            owner_uuid=owner_uuid,
            agent=agent,
            target_uuid=target_uuid,
            values=values,
            base_values=base_values,
            base_etag=base_etag,
            created=now,
            updated=now,
        )

    # Lifecycle transitions

    def _require_pending(self) -> None:
        if self.status != ActionStatus.PENDING:
            raise InvalidTransition(
                f"Action {self.id} is {self.status.value}, only pending actions allow this operation"
            )

    def _decide(
        self,
        status: ActionStatus,
        *,
        decided_by: str | None,
        result: str | None = None,
    ) -> None:
        self.status = status
        self.decided_by = decided_by
        self.decided_at = _now()
        self.updated = self.decided_at or _now()
        self.result = result

    def cancel(self, *, actor_uuid: str) -> None:
        """Owner withdraws its own proposal (iterative agents)."""
        if self.owner_uuid != actor_uuid:
            raise NotActionOwner(f"Action {self.id} does not belong to user {actor_uuid}")
        self._require_pending()
        self._decide(ActionStatus.CANCELLED, decided_by=actor_uuid, result="Cancelled by proposer")

    def approve(self, *, admin: str) -> None:
        """Administrator accepts the proposal; execution follows."""
        self._require_pending()
        self._decide(ActionStatus.APPROVED, decided_by=admin)

    def reject(self, *, admin: str, reason: str | None = None) -> None:
        """Administrator declines the proposal, with an optional reason."""
        self._require_pending()
        self._decide(ActionStatus.REJECTED, decided_by=admin, result=reason)

    def expire(self) -> None:
        """System-forced expiry (lazy cleanup on listing)."""
        self._require_pending()
        self._decide(ActionStatus.EXPIRED, decided_by=None, result="Expired")

    def set_result(self, *, result: str) -> None:
        """Record the execution summary of an approved action."""
        if self.status != ActionStatus.APPROVED:
            raise InvalidTransition(f"Action {self.id} is {self.status.value}, result requires approved")
        self.result = result
        self.updated = _now()

    def mark_failed(self, *, result: str) -> None:
        """Execution of an approved action failed."""
        if self.status != ActionStatus.APPROVED:
            raise InvalidTransition(f"Action {self.id} is {self.status.value}, only approved can fail")
        self.status = ActionStatus.FAILED
        self.result = result
        self.updated = _now()

    # (de)serialization

    def to_json(self) -> str:
        """Serialize to a stable JSON string (Storage payload)."""
        data = dataclasses.asdict(self)
        for key in ("created", "updated", "decided_at"):
            if data[key] is not None:
                data[key] = typing.cast(datetime.datetime, data[key]).isoformat()
        data["status"] = self.status.value
        return json.dumps(data, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "PendingAction":
        """Deserialize a :meth:`to_json` payload."""
        data = json.loads(raw)
        for key in ("created", "updated", "decided_at"):
            if data.get(key) is not None:
                data[key] = datetime.datetime.fromisoformat(typing.cast(str, data[key]))
        data["status"] = ActionStatus(data["status"])
        return cls(**data)
