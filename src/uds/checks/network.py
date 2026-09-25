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
Author: Andres Schumann, aschumann at virtualcable dot es

Checks on the addresses UDS sees for clients and actors.
"""

import collections
import ipaddress
import re
import typing

from django.utils.translation import gettext as _

from uds import models
from uds.core import types
from uds.core.checks import Check, CheckOutcome
from uds.core.util.config import GlobalConfig

from .logs import _LOGIN_RX, _window

_MAX_EXAMPLES: typing.Final[int] = 5

# Below this many logins in 24h the address distribution says nothing
_MIN_LOGINS: typing.Final[int] = 20
_MIN_USERS: typing.Final[int] = 3
_SINGLE_IP_SHARE: typing.Final[float] = 0.9

# Logged by ``uds.REST.methods.actor_v3.check_ip_is_blocked``
_ACTOR_BLOCKED_RX: typing.Final[re.Pattern[str]] = re.compile(r"Access to actor from (?P<ip>\S+) is blocked")


def _is_private(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


class ClientIpIsProxyAddressCheck(Check):
    """Logins that all come from one private address while "Behind a proxy" is off."""

    id: typing.ClassVar[str] = "client-ip-is-proxy-address"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH

    @typing.override
    def run(self) -> CheckOutcome:
        if GlobalConfig.BEHIND_PROXY.as_bool():
            return (
                types.checks.CheckSeverity.HIGH,
                True,
                _("'Behind a proxy' is enabled: client addresses are taken from X-Forwarded-For."),
            )

        users_by_ip: dict[str, set[str]] = collections.defaultdict(set)
        logins = 0
        for data in models.Log.objects.filter(
            created__gte=_window(),
            source=types.log.LogSource.WEB,
            owner_type=types.log.LogObjectType.AUTHENTICATOR,
        ).values_list("data", flat=True):
            match = _LOGIN_RX.match(data)
            if match:
                logins += 1
                users_by_ip[match.group("ip")].add(match.group("user"))

        if logins >= _MIN_LOGINS:
            ip, users = max(users_by_ip.items(), key=lambda item: len(item[1]))
            share = len(users) / len(set[str]().union(*users_by_ip.values()))
            if _is_private(ip) and len(users) >= _MIN_USERS and share >= _SINGLE_IP_SHARE:
                return (
                    types.checks.CheckSeverity.HIGH,
                    False,
                    _(
                        "{share:.0%} of the users that logged in during the last 24h came from {ip}, a private"
                        " address, and 'Behind a proxy' is disabled. UDS is probably seeing the address of a"
                        " load balancer instead of the client's: blocking by IP and IP-based access rules"
                        " apply to everybody at once. Enable 'Behind a proxy' and trust the balancer in"
                        " 'Allowed IP Forwarders'."
                    ).format(share=share, ip=ip),
                )
        return (
            types.checks.CheckSeverity.HIGH,
            True,
            _("Client addresses in the login log look like real client addresses."),
        )


class ActorIpsBlocked24hCheck(Check):
    """Addresses blocked for actor access in the last 24h."""

    id: typing.ClassVar[str] = "actor-ips-blocked-24h"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH

    @typing.override
    def run(self) -> CheckOutcome:
        ips: collections.Counter[str] = collections.Counter()
        for data in models.Log.objects.filter(
            created__gte=_window(),
            owner_type=types.log.LogObjectType.SYSLOG,
            data__contains="Access to actor from",
        ).values_list("data", flat=True):
            match = _ACTOR_BLOCKED_RX.search(data)
            if match:
                ips[match.group("ip")] += 1

        if ips:
            examples = ", ".join(ip for ip, _count in ips.most_common(_MAX_EXAMPLES))
            if len(ips) > _MAX_EXAMPLES:
                examples += "..."
            return (
                types.checks.CheckSeverity.HIGH,
                False,
                _(
                    "{count} address(es) were blocked for actor access in the last 24h: {examples}."
                    " Actors behind those addresses cannot report their state until the block expires."
                    " If several machines share one address (NAT, proxy), one failing actor blocks all of them."
                ).format(count=len(ips), examples=examples),
            )
        return (
            types.checks.CheckSeverity.HIGH,
            True,
            _("No addresses were blocked for actor access in the last 24h."),
        )
