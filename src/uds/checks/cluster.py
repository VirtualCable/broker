#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright notice,
#      this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright notice,
#      this list of conditions and the following disclaimer in the documentation
#      and/or other materials provided with the distribution.
#    * Neither the name of Virtual Cable S.L. nor the names of its contributors
#      may be used to endorse or promote products derived from this software
#      without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""
Author: Adolfo Gómez, dkmaster at dkmon dot com

Checks related to the cluster of UDS nodes and the database clocks.

With a single broker node and a single database server, clocks are almost
always in sync (except for DST transitions or NTP jumps). With several
nodes on any side, the wall clock read through the database (``sql_now``)
is only consistent if the database nodes are synchronized *and* every
broker node uses the same timezone when a naive database clock (MySQL) has
to be made aware. These checks surface both kinds of inconsistencies.
"""

import datetime
import typing

from django.db import connection
from django.utils import timezone as dj_timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from uds.core import types
from uds.core.checks import AutomaticCheck, CheckOutcome
from uds.core.util import cluster
from uds.core.util.config import GlobalConfig
from uds.core.util.model import sql_now

# A node is considered "fresh" if it has updated its cluster property recently.
# The SystemInformation job stores cluster info every 300 seconds, so 15 minutes
# (3 missed cycles) means the node is down (or was down since before the upgrade).
_NODE_STALE_AFTER: typing.Final[datetime.timedelta] = datetime.timedelta(minutes=15)
# Number of raw database clock samples taken for the spread check
_SPREAD_SAMPLES: typing.Final[int] = 6


def _fresh_nodes() -> list[cluster.UDSClusterNode]:
    """Returns the cluster nodes seen recently enough to be trusted."""
    limit = sql_now() - _NODE_STALE_AFTER
    return [node for node in cluster.enumerate_cluster_nodes() if node.last_seen >= limit]


def _sample_db_clock(count: int) -> list[datetime.datetime]:
    """Reads the raw database clock ``count`` times.

    The connection is closed and reopened before every sample so that, behind
    a load balancer, each sample may reach a different database node. Best
    effort: balancers that pin connections will always report a spread near
    zero, even on unsynchronized database clusters.
    """
    samples: list[datetime.datetime] = []
    for _i in range(count):
        connection.close()
        if connection.vendor in ("mysql", "microsoft", "postgresql"):
            with connection.cursor() as cursor:
                sentence = (
                    "SELECT CURRENT_TIMESTAMP(4)"
                    if connection.vendor in ("mysql", "postgresql")
                    else "SELECT CURRENT_TIMESTAMP"
                )
                cursor.execute(sentence)
                dt = (cursor.fetchone() or [dj_timezone.localtime()])[0]
        else:
            # Unknown vendor (i.e. sqlite on development): use local time,
            # same fallback as TimeTrack._fetch_sql_datetime
            dt = dj_timezone.localtime()
        if dj_timezone.is_naive(dt):
            dt = dj_timezone.make_aware(dt)
        samples.append(dt)
    return samples


class ClusterNodesTimezonesCheck(AutomaticCheck):
    """Cluster nodes use different timezones."""

    id: typing.ClassVar[str] = "cluster-nodes-timezones"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH
    description: typing.ClassVar[str] = gettext_noop(
        "Compares the timezone reported by every recently seen cluster node. Mixed timezones make each node "
        "interpret a naive database clock (MySQL) as a different instant, so the same job can be seen as due "
        "on one node and not on another. Align the server timezone (timedatectl) on every node."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        nodes = _fresh_nodes()
        if len(nodes) <= 1:
            return (
                types.checks.CheckSeverity.MEDIUM,
                True,
                _("Cluster has {count} fresh node(s), no timezone comparison possible.").format(
                    count=len(nodes)
                ),
            )

        by_tz: dict[str, list[str]] = {}
        for node in nodes:
            by_tz.setdefault(node.timezone, []).append(node.hostname)

        if len(by_tz) <= 1:
            return (
                types.checks.CheckSeverity.MEDIUM,
                True,
                _("All {count} fresh cluster nodes use the same timezone ({tz}).").format(
                    count=len(nodes), tz=nodes[0].timezone
                ),
            )

        examples = [f"{hostname} ({tz})" for tz, hostnames in by_tz.items() for hostname in hostnames]
        return (
            types.checks.CheckSeverity.MEDIUM,
            False,
            _(
                "Fresh cluster nodes use different timezones: {summary}. A naive database clock is interpreted"
                " on each node using its own timezone, so nodes disagree on what time it is."
            ).format(summary=", ".join(examples)),
            examples,
        )


class ClusterNodeClockDriftCheck(AutomaticCheck):
    """Cluster node clock drifted from the database clock."""

    id: typing.ClassVar[str] = "cluster-node-clock-drift"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH
    description: typing.ClassVar[str] = gettext_noop(
        "Compares the local clock of every recently seen cluster node against the database clock. Scheduling "
        "always uses the database clock, but the local clock stamps rows, tokens and logs, so a drifted node "
        "is usually a NTP problem worth fixing. Check the details for the drifted nodes."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        threshold = GlobalConfig.MAX_CLOCK_DRIFT.as_int()
        nodes = _fresh_nodes()
        if not nodes:
            return (
                types.checks.CheckSeverity.MEDIUM,
                True,
                _("No fresh cluster node found, no clock drift comparison possible."),
            )

        drifting: list[str] = []
        for node in nodes:
            if node.db_offset is None:
                continue  # Node has not reported a measurement yet
            if abs(node.db_offset) > threshold:
                drifting.append(f"{node.hostname} ({node.db_offset:+.1f}s)")

        if drifting:
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _(
                    "{count} cluster node(s) have a local clock drifted more than {threshold}s from the"
                    " database clock: {examples}. Fix the time synchronization (NTP) on those nodes."
                ).format(count=len(drifting), threshold=threshold, examples=", ".join(drifting)),
                drifting,
            )
        return (
            types.checks.CheckSeverity.MEDIUM,
            True,
            _("All {count} fresh cluster node(s) are within {threshold}s of the database clock.").format(
                count=len(nodes), threshold=threshold
            ),
        )


class DbClockSpreadCheck(AutomaticCheck):
    """Database cluster clocks are not synchronized."""

    id: typing.ClassVar[str] = "db-clock-spread"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH
    description: typing.ClassVar[str] = gettext_noop(
        "Reads the raw database clock several times through consecutive connections and measures the spread. "
        "Behind a load balanced database cluster, a big spread means the database nodes themselves are not "
        "time synchronized, which breaks every scheduling decision. Fix NTP on the database servers."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        threshold = GlobalConfig.MAX_DB_CLOCK_SPREAD.as_int()
        samples = _sample_db_clock(_SPREAD_SAMPLES)
        if len(samples) < 2:
            return (
                types.checks.CheckSeverity.MEDIUM,
                True,
                _("Could not take enough database clock samples, no spread comparison possible."),
            )

        spread = (max(samples) - min(samples)).total_seconds()
        if spread > threshold:
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _(
                    "Database clock spread across {count} samples is {spread:.2f}s, above the {threshold}s"
                    " threshold. The database nodes are probably not time synchronized."
                ).format(count=len(samples), spread=spread, threshold=threshold),
            )
        return (
            types.checks.CheckSeverity.MEDIUM,
            True,
            _("Database clock spread across {count} samples is {spread:.2f}s, within limits.").format(
                count=len(samples), spread=spread
            ),
        )
