
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

Security self-assessment checks for the OpenUDS broker.

Each check evaluates a single security-relevant configuration condition and
returns its severity, whether it passes and a human readable detail.

Checks only *notify*: they never modify any configuration value.
"""

import collections.abc
import logging
import typing

from django.conf import settings

from uds.core import types
from uds.core import consts
from uds.core.managers.crypto import CryptoManager
from uds.core.util.config import GlobalConfig
from uds.models import Properties, ServicePool, UserService

logger = logging.getLogger(__name__)

# Shipped default of ``GlobalConfig.SUPER_USER_PASS`` (see uds.core.util.config).
# Used to detect installations where the root password has never been rotated.
DEFAULT_SUPERUSER_PASSWORD: typing.Final[str] = "udsmam0"

# A check returns ``(severity, ok, message)``
_CheckResult: typing.TypeAlias = tuple[types.security.SecurityCheckSeverity, bool, str]
_CheckFunction: typing.TypeAlias = collections.abc.Callable[[], _CheckResult]


def _check_default_superuser_credentials() -> _CheckResult:
    stored = GlobalConfig.SUPER_USER_PASS.get(True)
    # Both the raw comparison (legacy unhashed storage) and the hash check
    # (modern installs store an Argon2 hash of the password) are needed.
    if stored == DEFAULT_SUPERUSER_PASSWORD or CryptoManager.manager().check_hash(DEFAULT_SUPERUSER_PASSWORD, stored):
        return (
            types.security.SecurityCheckSeverity.CRITICAL,
            False,
            "Default superuser credentials are still active. Change the root password immediately.",
        )
    return (
        types.security.SecurityCheckSeverity.CRITICAL,
        True,
        "Superuser password is not the shipped default.",
    )


def _check_superuser_web_access() -> _CheckResult:
    if GlobalConfig.SUPER_USER_ALLOW_WEBACCESS.as_bool(True):
        return (
            types.security.SecurityCheckSeverity.MEDIUM,
            False,
            "Root web/API access is enabled (SUPER_USER_ALLOW_WEBACCESS). Disable it if not needed in production.",
        )
    return (
        types.security.SecurityCheckSeverity.MEDIUM,
        True,
        "Root web/API access is disabled.",
    )


def _check_trusted_sources_wildcard() -> _CheckResult:
    wildcards: list[str] = []
    if GlobalConfig.TRUSTED_SOURCES.get(True).strip() == "*":
        wildcards.append("TRUSTED_SOURCES")
    if GlobalConfig.ADMIN_TRUSTED_SOURCES.get(True).strip() == "*":
        wildcards.append("ADMIN_TRUSTED_SOURCES")
    if wildcards:
        return (
            types.security.SecurityCheckSeverity.MEDIUM,
            False,
            f"{', '.join(wildcards)} set to wildcard (*): IP-based gating for tunnels, actors and admin operations is disabled.",
        )
    return (
        types.security.SecurityCheckSeverity.MEDIUM,
        True,
        "TRUSTED_SOURCES and ADMIN_TRUSTED_SOURCES are not wildcards.",
    )


def _check_ip_forwarders_wildcard() -> _CheckResult:
    if not GlobalConfig.BEHIND_PROXY.as_bool(True):
        return (
            types.security.SecurityCheckSeverity.INFO,
            True,
            "Broker is not behind a proxy; the default wildcard ALLOWED_IP_FORWARDERS is not exploitable until BEHIND_PROXY is enabled.",
        )
    if GlobalConfig.ALLOWED_IP_FORWARDERS.get(True).strip() == "*":
        return (
            types.security.SecurityCheckSeverity.HIGH,
            False,
            "Broker is behind a proxy and ALLOWED_IP_FORWARDERS is a wildcard: any client can spoof X-Forwarded-For."
            " Restrict it to the actual proxy addresses.",
        )
    return (
        types.security.SecurityCheckSeverity.HIGH,
        True,
        "Broker is behind a proxy and ALLOWED_IP_FORWARDERS is restricted to concrete addresses.",
    )


def _check_security_cookies_and_headers() -> _CheckResult:
    flags: dict[str, bool] = {
        "SESSION_COOKIE_HTTPONLY": settings.SESSION_COOKIE_HTTPONLY,
        "SESSION_COOKIE_SECURE": settings.SESSION_COOKIE_SECURE,
        "CSRF_COOKIE_HTTPONLY": settings.CSRF_COOKIE_HTTPONLY,
        "CSRF_COOKIE_SECURE": settings.CSRF_COOKIE_SECURE,
        "ENHANCED_SECURITY": GlobalConfig.ENHANCED_SECURITY.as_bool(True),
    }
    missing = ", ".join(name for name, enabled in flags.items() if not enabled)
    if missing:
        return (
            types.security.SecurityCheckSeverity.LOW,
            False,
            f"Security cookies/headers are disabled: {missing}.",
        )
    return (
        types.security.SecurityCheckSeverity.LOW,
        True,
        "Session/security cookies and enhanced security are enabled.",
    )


def _check_saml_assertions_signed() -> _CheckResult:
    # Imported here to avoid loading authenticator modules (and their optional
    # dependencies) unless this check actually runs.
    from uds import models
    from uds.auths.SAML import SAMLAuthenticator

    authenticator: models.Authenticator
    unsigned: list[str] = []
    found = False
    for authenticator in models.Authenticator.objects.all():
        if authenticator.get_type() is not SAMLAuthenticator:
            continue
        found = True
        instance = typing.cast("SAMLAuthenticator", authenticator.get_instance())
        if not (instance.want_assertions_signed.as_bool() or instance.want_messages_signed.as_bool()):
            unsigned.append(authenticator.name)

    if unsigned:
        return (
            types.security.SecurityCheckSeverity.MEDIUM,
            False,
            f"SAML assertions are not required to be signed on: {', '.join(unsigned)}."
            " Unsigned assertions can be forged against a misconfigured IdP.",
        )
    if found:
        return (
            types.security.SecurityCheckSeverity.MEDIUM,
            True,
            "All SAML authenticators require signed assertions or messages.",
        )
    return (
        types.security.SecurityCheckSeverity.MEDIUM,
        True,
        "No SAML authenticators configured.",
    )


def _check_old_token_used_by_actor() -> _CheckResult:
    # A user service is considered "still using the legacy flow" when its token
    # has never been rotated (AUTO_TOKEN_PREFIX_NOT_USED) AND the actor has
    # actually reported a version (i.e. an actor connected to it), which means it
    # is still authenticating with the old uuid-based token.
    legacy_actor_services = UserService.objects.filter(
        token__startswith=consts.auth.AUTO_TOKEN_PREFIX_NOT_USED,
        uuid__in=Properties.objects.filter(
            owner_type="userservice",
            key="actor_version",
        )
        .exclude(value="0.0.0")
        .values("owner_id"),
    )

    affected_pools = ServicePool.objects.filter(userServices__in=legacy_actor_services).distinct().order_by("name")

    if affected_pools:
        affected = ", ".join(pool.name for pool in affected_pools)
        return (
            types.security.SecurityCheckSeverity.MEDIUM,
            False,
            f"Service pools with actors still using the legacy uuid token flow: {affected}."
            " Re-initialize affected actors to rotate them to the new token.",
        )
    return (
        types.security.SecurityCheckSeverity.MEDIUM,
        True,
        "No user services are using the legacy uuid actor token flow.",
    )


_CHECKS: typing.Final[tuple[tuple[str, _CheckFunction], ...]] = (
    ("default-superuser-credentials", _check_default_superuser_credentials),
    ("superuser-web-access", _check_superuser_web_access),
    ("trusted-sources-wildcard", _check_trusted_sources_wildcard),
    ("ip-forwarders-wildcard", _check_ip_forwarders_wildcard),
    ("security-cookies-and-headers", _check_security_cookies_and_headers),
    ("saml-assertions-signed", _check_saml_assertions_signed),
    ("old-token-used-by-actor", _check_old_token_used_by_actor),
)


def run_security_checks() -> list[types.security.SecurityCheckResult]:
    """
    Runs all the registered security checks and returns their results.

    A check that raises is reported as a failed ``INFO`` result instead of
    aborting the whole scan, so a single broken check never hides the rest.
    """
    results: list[types.security.SecurityCheckResult] = []
    for check_id, check in _CHECKS:
        try:
            severity, ok, message = check()
        except Exception as e:
            logger.exception("Security check %s could not be evaluated", check_id)
            results.append(
                types.security.SecurityCheckResult(
                    id=check_id,
                    severity=types.security.SecurityCheckSeverity.INFO,
                    ok=False,
                    message=f"Check could not be evaluated: {e}",
                )
            )
            continue
        results.append(types.security.SecurityCheckResult(id=check_id, severity=severity, ok=ok, message=message))

    return results


def build_report(results: list[types.security.SecurityCheckResult] | None = None) -> dict[str, typing.Any]:
    """
    Aggregates the check results into the JSON report returned by the
    ``/system/security_check`` endpoint: a summary with the number of failed
    checks per severity plus the full check list.

    If ``results`` is ``None`` the checks are run first.
    """
    if results is None:
        results = run_security_checks()

    report: dict[str, typing.Any] = {severity.value: 0 for severity in types.security.SecurityCheckSeverity}
    for result in results:
        if not result.ok:
            report[result.severity.value] += 1
    report["checks"] = [result.as_dict() for result in results]

    return report
