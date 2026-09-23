"""
Notification related types.

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import dataclasses
import enum


@dataclasses.dataclass(frozen=True)
class NotificationGroupInfo:
    """
    Metadata associated to a NotificationGroup member.
    """

    # Literal value stored on database (uds_notification.group)
    kind: str
    # If True, notifications of this group are delivered even if identical ones
    # have been delivered recently (no deduplication is applied on processing)
    repeatable: bool


class NotificationGroup(enum.Enum):
    """
    Groups of notifications.

    Each member carries a NotificationGroupInfo with its metadata. Notifiers declare
    which groups they handle through their "accepts" class variable, and the
    notification processor uses "repeatable" to decide if deduplication applies.
    """

    # Classic log notifications. Deduplicated on processing to avoid flooding
    # notifiers (mail, telegram, ...) with repeated messages
    LOG = NotificationGroupInfo(kind="log", repeatable=False)
    # Event notifications. Every occurrence is delivered, no deduplication applies
    EVENT = NotificationGroupInfo(kind="event", repeatable=True)

    @property
    def kind(self) -> str:
        """
        Literal value of this group, as stored on database.
        """
        return self.value.kind

    @property
    def repeatable(self) -> bool:
        """
        If True, identical notifications of this group are not deduplicated.
        """
        return self.value.repeatable

    def __str__(self) -> str:
        return self.value.kind

    def __repr__(self) -> str:
        return self.value.kind

    @classmethod
    def from_kind(cls, kind: str, default: "NotificationGroup | None" = None) -> "NotificationGroup":
        """
        Returns the group for a given kind (as stored on database).

        Args:
            kind: Literal value of the group (e.g. "log", "event")
            default: Value to return if kind is unknown. If None, a ValueError is raised

        Raises:
            ValueError: If kind is unknown and no default is provided
        """
        for group in cls:
            if group.value.kind == kind:
                return group
        if default is None:
            raise ValueError(f"Unknown notification group kind: {kind}")
        return default
