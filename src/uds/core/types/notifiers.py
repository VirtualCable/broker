"""
Notification related types.

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import dataclasses
import enum
import typing

if typing.TYPE_CHECKING:
    from uds.core.util import log


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


class EventType(enum.StrEnum):
    """
    Auditable event types, delivered as NotificationGroup.EVENT notifications.

    Values are prefixed by domain ("admin.", "user.") so third party consumers
    (webhook notifiers, ...) can filter by domain or by exact value.

    The "identificator" of the resulting notification is this value, and the
    "message" is a human readable description of the occurrence.
    """

    # Administrative CRUD (REST model handlers, master and detail)
    ADMIN_CREATE = "admin.create"
    ADMIN_MODIFY = "admin.modify"
    ADMIN_DELETE = "admin.delete"
    # User authentication
    LOGIN = "user.login"
    LOGIN_FAILED = "user.login_failed"
    LOGOUT = "user.logout"
    # User session on a user service (VM, published app, RDS session, ...)
    USER_SERVICE_LOGIN = "userservice.login"
    USER_SERVICE_LOGOUT = "userservice.logout"

    def notify(self, message: str, level: "log.LogLevel | None" = None) -> None:
        """
        Notifies this event as a NotificationGroup.EVENT notification.

        Args:
            message: Human readable description of the occurrence
                (who, where, what)
            level: Notification level. If None (default), LogLevel.OTHER is
                used, so the event reaches every EVENT notifier regardless of
                its configured minimum level
        """
        # Imported here to avoid circular imports (types <-> managers / util.log)
        from uds.core.managers import notifications  # pylint: disable=import-outside-toplevel
        from uds.core.util import log  # pylint: disable=import-outside-toplevel

        notifications.NotificationsManager.manager().notify(
            NotificationGroup.EVENT, self.value, level or log.LogLevel.OTHER, message
        )
