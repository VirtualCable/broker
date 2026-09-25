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

Checks derived from ``uds.core.util.config.GlobalConfig``.

Grouped here because they all read their state from the admin-editable global
configuration (DB-backed) rather than from ``django.conf.settings`` or live
database rows.
"""

import typing

from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from uds.core import consts, types
from uds.core.checks import Check, CheckOutcome
from uds.core.managers.crypto import CryptoManager
from uds.core.util.config import GlobalConfig


class DefaultSuperuserCredentialsCheck(Check):
    """Root password left at the shipped default."""

    id: typing.ClassVar[str] = "default-superuser-credentials"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY
    description: typing.ClassVar[str] = gettext_noop(
        "Verifies that the superuser (root) password is not the default one. A known password "
        "gives full administration access. Change SUPER_USER_PASS in the global configuration."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        stored = GlobalConfig.SUPER_USER_PASS.get(True)
        # Both the raw comparison (legacy unhashed storage) and the hash check
        # (modern installs store an Argon2 hash of the password) are needed.
        if stored == consts.security.DEFAULT_SUPERUSER_PASSWORD or CryptoManager.manager().check_hash(
            consts.security.DEFAULT_SUPERUSER_PASSWORD, stored
        ):
            return (
                types.checks.CheckSeverity.CRITICAL,
                False,
                _("Default superuser credentials are still active. Change the root password immediately."),
            )
        return (
            types.checks.CheckSeverity.CRITICAL,
            True,
            _("Superuser password is not the shipped default."),
        )


class SuperuserWebAccessCheck(Check):
    """Root web/API access enabled."""

    id: typing.ClassVar[str] = "superuser-web-access"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY
    description: typing.ClassVar[str] = gettext_noop(
        "Verifies whether the superuser can log in to the administration and the REST API "
        "(SUPER_USER_ALLOW_WEBACCESS). If it is not needed in production, disable it and use "
        "administrators from an authenticator instead."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        if GlobalConfig.SUPER_USER_ALLOW_WEBACCESS.as_bool(True):
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _(
                    "Root web/API access is enabled (SUPER_USER_ALLOW_WEBACCESS). Disable it if not needed in production."
                ),
            )
        return (
            types.checks.CheckSeverity.MEDIUM,
            True,
            _("Root web/API access is disabled."),
        )


class TrustedSourcesWildcardCheck(Check):
    """Trusted sources left as wildcard."""

    id: typing.ClassVar[str] = "trusted-sources-wildcard"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY
    description: typing.ClassVar[str] = gettext_noop(
        "Verifies that TRUSTED_SOURCES and ADMIN_TRUSTED_SOURCES are not '*'. These lists limit "
        "which addresses can reach the tunnel, actor and administration endpoints. Set them to "
        "the networks of your tunnels, machines and administrators."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        wildcards: list[str] = []
        if GlobalConfig.TRUSTED_SOURCES.get(True).strip() == "*":
            wildcards.append("TRUSTED_SOURCES")
        if GlobalConfig.ADMIN_TRUSTED_SOURCES.get(True).strip() == "*":
            wildcards.append("ADMIN_TRUSTED_SOURCES")
        if wildcards:
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _(
                    "{wildcards} set to wildcard (*): IP-based gating for tunnels, actors and admin operations is disabled."
                ).format(wildcards=", ".join(wildcards)),
            )
        return (
            types.checks.CheckSeverity.MEDIUM,
            True,
            _("TRUSTED_SOURCES and ADMIN_TRUSTED_SOURCES are not wildcards."),
        )


class IpForwardersWildcardCheck(Check):
    """ALLOWED_IP_FORWARDERS wildcard while behind a proxy."""

    id: typing.ClassVar[str] = "ip-forwarders-wildcard"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY
    description: typing.ClassVar[str] = gettext_noop(
        "When the broker is behind a proxy (BEHIND_PROXY), verifies that ALLOWED_IP_FORWARDERS is "
        "not '*'. Otherwise any client can fake its address with an X-Forwarded-For header and "
        "get around the IP based rules. Set it to the addresses of your proxies or load "
        "balancers."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        if not GlobalConfig.BEHIND_PROXY.as_bool(True):
            return (
                types.checks.CheckSeverity.INFO,
                True,
                _(
                    "Broker is not behind a proxy; the default wildcard ALLOWED_IP_FORWARDERS is not exploitable until BEHIND_PROXY is enabled."
                ),
            )
        if GlobalConfig.ALLOWED_IP_FORWARDERS.get(True).strip() == "*":
            return (
                types.checks.CheckSeverity.HIGH,
                False,
                _(
                    "Broker is behind a proxy and ALLOWED_IP_FORWARDERS is a wildcard: any client can spoof X-Forwarded-For."
                    " Restrict it to the actual proxy addresses."
                ),
            )
        return (
            types.checks.CheckSeverity.HIGH,
            True,
            _("Broker is behind a proxy and ALLOWED_IP_FORWARDERS is restricted to concrete addresses."),
        )


class LoginHardeningWeakCheck(Check):
    """Login hardening knobs outside recommended ranges."""

    id: typing.ClassVar[str] = "login-hardening-weak"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY
    description: typing.ClassVar[str] = gettext_noop(
        "Verifies the login lockout settings: MAX_LOGIN_TRIES between 1 and 20, LOGIN_BLOCK of at "
        "least 30 seconds and LOGIN_BLOCK_IP enabled. Weaker values make password guessing "
        "easier. Adjust them in the global configuration."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        # Bundle of related knobs. Each one is reported separately so the operator
        # can see which one is off without re-reading the check.
        issues: list[str] = []
        max_tries = GlobalConfig.MAX_LOGIN_TRIES.as_int()
        block_seconds = GlobalConfig.LOGIN_BLOCK.as_int()
        block_ip = GlobalConfig.LOGIN_BLOCK_IP.as_bool(True)

        if max_tries > 20 or max_tries <= 0:
            issues.append(_("MAX_LOGIN_TRIES={value} (recommended: 1-20)").format(value=max_tries))
        if block_seconds < 30:
            issues.append(_("LOGIN_BLOCK={value}s (recommended: >= 30)").format(value=block_seconds))
        if not block_ip:
            issues.append(_("LOGIN_BLOCK_IP is off (recommended: on)"))

        if issues:
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _("Login hardening knobs are weak: {issues}.").format(issues="; ".join(issues)),
            )
        return (
            types.checks.CheckSeverity.MEDIUM,
            True,
            _("Login hardening knobs are within recommended ranges."),
        )


class ActorFailureBlockingDisabledCheck(Check):
    """Actor failure blocking disabled."""

    id: typing.ClassVar[str] = "actor-failure-blocking-disabled"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY
    description: typing.ClassVar[str] = gettext_noop(
        "Verifies that BLOCK_ACTOR_FAILURES is enabled, so the addresses that send wrong tokens "
        "to the actor endpoints get blocked. Without it, tokens can be tried without limit. "
        "Enable it in the global configuration."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        if GlobalConfig.BLOCK_ACTOR_FAILURES.as_bool(True):
            return (
                types.checks.CheckSeverity.MEDIUM,
                True,
                _("Actor failure blocking is enabled (BLOCK_ACTOR_FAILURES)."),
            )
        return (
            types.checks.CheckSeverity.MEDIUM,
            False,
            _(
                "Actor failure blocking is disabled (BLOCK_ACTOR_FAILURES): /actor/ endpoints"
                " have no rate limit on bad tokens."
            ),
        )


class ExperimentalFeaturesOnCheck(Check):
    """Experimental features exposed."""

    id: typing.ClassVar[str] = "experimental-features-on"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY
    description: typing.ClassVar[str] = gettext_noop(
        "Verifies that EXPERIMENTAL_FEATURES is off. Experimental features are not supported and "
        "may change or break. Disable it in the global configuration unless you are testing one "
        "of them."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        if GlobalConfig.EXPERIMENTAL_FEATURES.as_bool(True):
            return (
                types.checks.CheckSeverity.LOW,
                False,
                _("EXPERIMENTAL_FEATURES is on: unsupported functionality is exposed."),
            )
        return (
            types.checks.CheckSeverity.LOW,
            True,
            _("EXPERIMENTAL_FEATURES is off."),
        )


class ImmutableAuditLogOffCheck(Check):
    """Immutable (TSA-signed) audit log disabled."""

    id: typing.ClassVar[str] = "immutable-audit-log-off"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY
    description: typing.ClassVar[str] = gettext_noop(
        "Verifies whether the immutable audit log (IMMUTABLE_LOG_ENABLED) is enabled. When it is, "
        "logins and administration events are also written to a tamper-evident log signed by a "
        "time stamping authority. Enable it if you need an audit trail that cannot be altered."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        if GlobalConfig.IMMUTABLE_LOG_ENABLED.as_bool(True):
            return (
                types.checks.CheckSeverity.INFO,
                True,
                _("Immutable audit log is enabled (TSA-signed)."),
            )
        return (
            types.checks.CheckSeverity.INFO,
            False,
            _(
                "IMMUTABLE_LOG_ENABLED is off: logins and admin events are not written to"
                " the tamper-evident log (src/uds/models/immutable_log.py)."
            ),
        )
