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

Security checks derived from ``uds.core.util.config.GlobalConfig``.

Grouped here because they all read their state from the admin-editable global
configuration (DB-backed) rather than from ``django.conf.settings`` or live
database rows.
"""

from django.utils.translation import gettext as _

from uds.core import consts, types
from uds.core.managers.crypto import CryptoManager
from uds.core.util.config import GlobalConfig

from .factory import SecurityChecksFactory


def _check_default_superuser_credentials() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    stored = GlobalConfig.SUPER_USER_PASS.get(True)
    # Both the raw comparison (legacy unhashed storage) and the hash check
    # (modern installs store an Argon2 hash of the password) are needed.
    if stored == consts.security.DEFAULT_SUPERUSER_PASSWORD or CryptoManager.manager().check_hash(
        consts.security.DEFAULT_SUPERUSER_PASSWORD, stored
    ):
        return (
            types.security.SecurityCheckSeverity.CRITICAL,
            False,
            _("Default superuser credentials are still active. Change the root password immediately."),
        )
    return (
        types.security.SecurityCheckSeverity.CRITICAL,
        True,
        _("Superuser password is not the shipped default."),
    )


def _check_superuser_web_access() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    if GlobalConfig.SUPER_USER_ALLOW_WEBACCESS.as_bool(True):
        return (
            types.security.SecurityCheckSeverity.MEDIUM,
            False,
            _("Root web/API access is enabled (SUPER_USER_ALLOW_WEBACCESS). Disable it if not needed in production."),
        )
    return (
        types.security.SecurityCheckSeverity.MEDIUM,
        True,
        _("Root web/API access is disabled."),
    )


def _check_trusted_sources_wildcard() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    wildcards: list[str] = []
    if GlobalConfig.TRUSTED_SOURCES.get(True).strip() == "*":
        wildcards.append("TRUSTED_SOURCES")
    if GlobalConfig.ADMIN_TRUSTED_SOURCES.get(True).strip() == "*":
        wildcards.append("ADMIN_TRUSTED_SOURCES")
    if wildcards:
        return (
            types.security.SecurityCheckSeverity.MEDIUM,
            False,
            _(
                "{wildcards} set to wildcard (*): IP-based gating for tunnels, actors and admin operations is disabled."
            ).format(wildcards=", ".join(wildcards)),
        )
    return (
        types.security.SecurityCheckSeverity.MEDIUM,
        True,
        _("TRUSTED_SOURCES and ADMIN_TRUSTED_SOURCES are not wildcards."),
    )


def _check_ip_forwarders_wildcard() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    if not GlobalConfig.BEHIND_PROXY.as_bool(True):
        return (
            types.security.SecurityCheckSeverity.INFO,
            True,
            _(
                "Broker is not behind a proxy; the default wildcard ALLOWED_IP_FORWARDERS is not exploitable until BEHIND_PROXY is enabled."
            ),
        )
    if GlobalConfig.ALLOWED_IP_FORWARDERS.get(True).strip() == "*":
        return (
            types.security.SecurityCheckSeverity.HIGH,
            False,
            _(
                "Broker is behind a proxy and ALLOWED_IP_FORWARDERS is a wildcard: any client can spoof X-Forwarded-For."
                " Restrict it to the actual proxy addresses."
            ),
        )
    return (
        types.security.SecurityCheckSeverity.HIGH,
        True,
        _("Broker is behind a proxy and ALLOWED_IP_FORWARDERS is restricted to concrete addresses."),
    )


def _check_login_hardening_weak() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
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
            types.security.SecurityCheckSeverity.MEDIUM,
            False,
            _("Login hardening knobs are weak: {issues}.").format(issues="; ".join(issues)),
        )
    return (
        types.security.SecurityCheckSeverity.MEDIUM,
        True,
        _("Login hardening knobs are within recommended ranges."),
    )


def _check_actor_failure_blocking_disabled() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    if GlobalConfig.BLOCK_ACTOR_FAILURES.as_bool(True):
        return (
            types.security.SecurityCheckSeverity.MEDIUM,
            True,
            _("Actor failure blocking is enabled (BLOCK_ACTOR_FAILURES)."),
        )
    return (
        types.security.SecurityCheckSeverity.MEDIUM,
        False,
        _(
            "Actor failure blocking is disabled (BLOCK_ACTOR_FAILURES): /actor/ endpoints"
            " have no rate limit on bad tokens."
        ),
    )


def _check_honor_client_ip_notify() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    if GlobalConfig.HONOR_CLIENT_IP_NOTIFY.as_bool(True):
        return (
            types.security.SecurityCheckSeverity.LOW,
            False,
            _(
                "HONOR_CLIENT_IP_NOTIFY is on: clients can self-report their IP, which weakens"
                " IP-based logging and blocking. Only acceptable on trusted deployments."
            ),
        )
    return (
        types.security.SecurityCheckSeverity.LOW,
        True,
        _("HONOR_CLIENT_IP_NOTIFY is off: server-detected IP is authoritative."),
    )


def _check_session_duration_excessive() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    admin_seconds = GlobalConfig.SESSION_DURATION_ADMIN.as_int()
    user_seconds = GlobalConfig.SESSION_DURATION_USER.as_int()
    threshold = 12 * 3600  # 12 hours
    excessive: list[str] = []
    if admin_seconds > threshold:
        excessive.append(_("SESSION_DURATION_ADMIN={value}s").format(value=admin_seconds))
    if user_seconds > threshold:
        excessive.append(_("SESSION_DURATION_USER={value}s").format(value=user_seconds))
    if excessive:
        return (
            types.security.SecurityCheckSeverity.LOW,
            False,
            _("Session durations exceed 12h: {issues}.").format(issues="; ".join(excessive)),
        )
    return (
        types.security.SecurityCheckSeverity.LOW,
        True,
        _("Admin and user session durations are within 12h."),
    )


def _check_experimental_features_on() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    if GlobalConfig.EXPERIMENTAL_FEATURES.as_bool(True):
        return (
            types.security.SecurityCheckSeverity.LOW,
            False,
            _("EXPERIMENTAL_FEATURES is on: unsupported functionality is exposed."),
        )
    return (
        types.security.SecurityCheckSeverity.LOW,
        True,
        _("EXPERIMENTAL_FEATURES is off."),
    )


def _check_zero_trust_off() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    if GlobalConfig.ENFORCE_ZERO_TRUST.as_bool(True):
        return (
            types.security.SecurityCheckSeverity.INFO,
            True,
            _("ENFORCE_ZERO_TRUST is on: password redirection is disabled."),
        )
    return (
        types.security.SecurityCheckSeverity.INFO,
        True,
        _("ENFORCE_ZERO_TRUST is off (password redirection allowed)."),
    )


def _check_immutable_audit_log_off() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    if GlobalConfig.IMMUTABLE_LOG_ENABLED.as_bool(True):
        return (
            types.security.SecurityCheckSeverity.INFO,
            True,
            _("Immutable audit log is enabled (TSA-signed)."),
        )
    return (
        types.security.SecurityCheckSeverity.INFO,
        False,
        _(
            "IMMUTABLE_LOG_ENABLED is off: logins and admin events are not written to"
            " the tamper-evident log (src/uds/models/immutable_log.py)."
        ),
    )


def register_checks(factory: SecurityChecksFactory) -> None:
    """Registers the global-config checks into the shared factory."""
    factory.register_check("default-superuser-credentials", _check_default_superuser_credentials)
    factory.register_check("superuser-web-access", _check_superuser_web_access)
    factory.register_check("trusted-sources-wildcard", _check_trusted_sources_wildcard)
    factory.register_check("ip-forwarders-wildcard", _check_ip_forwarders_wildcard)
    factory.register_check("login-hardening-weak", _check_login_hardening_weak)
    factory.register_check("actor-failure-blocking-disabled", _check_actor_failure_blocking_disabled)
    factory.register_check("honor-client-ip-notify", _check_honor_client_ip_notify)
    factory.register_check("session-duration-excessive", _check_session_duration_excessive)
    factory.register_check("experimental-features-on", _check_experimental_features_on)
    factory.register_check("zero-trust-off", _check_zero_trust_off)
    factory.register_check("immutable-audit-log-off", _check_immutable_audit_log_off)
