import datetime
import logging
import socket
import time
import typing

from django.conf import settings
from django.db import OperationalError
from django.db import transaction
from django.utils import timezone

from uds import models
from uds.core import consts
from uds.core.util.iface import get_first_iface
from uds.core.util.model import get_my_ip_from_db
from uds.core.util.model import sql_now

logger: logging.Logger = logging.getLogger(__name__)


class UDSClusterNode(typing.NamedTuple):
    """
    Represents a node in the cluster with its hostname and last seen date.
    """

    hostname: str
    ip: str
    last_seen: datetime.datetime
    mac: str = consts.NULL_MAC
    # IANA timezone of the node. Nodes that have not reported it yet (rows
    # stored by older versions, or nodes down since the upgrade) are assumed
    # to share the timezone of the node doing the lookup: any active node
    # refreshes its own row within minutes.
    timezone: str = ""
    # Offset in seconds between the node local clock and the database clock,
    # measured by the node itself when storing its info. None if the node has
    # not reported a measurement yet.
    db_offset: float | None = None

    def as_dict(self) -> dict[str, str | float | None]:
        """
        Returns a dictionary representation of the UDSClusterNode.
        """
        return {
            "hostname": self.hostname,
            "ip": self.ip,
            "last_seen": self.last_seen.isoformat(),
            "mac": self.mac,
            "timezone": self.timezone,
            "db_offset": self.db_offset,
        }

    def __str__(self) -> str:
        return f"{self.hostname} ({self.ip}) - Last seen: {self.last_seen.isoformat()} - MAC: {self.mac}"


def store_cluster_info() -> None:
    """
    Stores the current hostname in the database, ensuring that it is unique.
    This is used to identify the current node in a cluster.
    """
    iface = get_first_iface()
    ip = iface.ip if iface else get_my_ip_from_db()
    mac = iface.mac if iface else consts.NULL_MAC

    try:
        hostname = socket.getfqdn() + "|" + ip
        date = sql_now()
        with transaction.atomic():
            current_host_property = (
                models.Properties.objects.select_for_update()
                .filter(owner_id="cluster", owner_type="cluster", key=hostname)
                .first()
            )
            value: dict[str, str | float] = {
                "last_seen": date.isoformat(),
                "mac": mac,
                "timezone": settings.TIME_ZONE,
                # Offset between this node local clock and the database clock,
                # in seconds. Measured here, while storing, so consumers can
                # detect nodes with unsynchronized clocks later.
                "db_offset": round(time.time() - date.timestamp(), 3),
            }
            if current_host_property:
                # Update existing property
                current_host_property.value = value
                current_host_property.save()
            else:
                # Create new property
                models.Properties.objects.create(
                    owner_id="cluster", owner_type="cluster", key=hostname, value=value
                )

    except OperationalError as e:
        # If we cannot connect to the database, we log the error
        logger.error("Could not store cluster hostname: %s", e)


def enumerate_cluster_nodes() -> list[UDSClusterNode]:
    """
    Enumerates all nodes in the cluster by fetching properties with owner_type 'cluster'.
    Returns a list of hostnames.
    """
    try:
        properties = models.Properties.objects.filter(owner_type="cluster")
        nodes: list[UDSClusterNode] = []
        for prop in properties:
            if "last_seen" not in prop.value or "|" not in prop.key:
                continue
            last_seen = datetime.datetime.fromisoformat(prop.value["last_seen"])
            if timezone.is_naive(last_seen):
                last_seen = timezone.make_aware(last_seen)
            nodes.append(
                UDSClusterNode(
                    hostname=prop.key.split("|")[0],
                    ip=prop.key.split("|")[1],
                    last_seen=last_seen,
                    mac=prop.value.get("mac", consts.NULL_MAC),
                    timezone=prop.value.get("timezone", settings.TIME_ZONE),
                    db_offset=prop.value.get("db_offset"),
                )
            )
        return nodes
    except OperationalError as e:
        # If we cannot connect to the database, we log the error and return an empty list
        logger.error("Could not enumerate cluster nodes: %s", e)
        return []
