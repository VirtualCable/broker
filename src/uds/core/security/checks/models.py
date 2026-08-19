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

Security checks derived from live database state (models).

Grouped here because they all inspect stored configuration rows
(authenticators, user services, ...) rather than ``django.conf.settings``
or the admin-editable global config.
"""

import typing

from django.utils.translation import gettext as _

from uds import models
from uds.core import consts, types

from .factory import SecurityChecksFactory


def _check_saml_assertions_signed() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    # Imported here to avoid loading authenticator modules (and their optional
    # dependencies) unless this check actually runs.
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
            _(
                "SAML assertions are not required to be signed on: {unsigned}."
                " Unsigned assertions can be forged against a misconfigured IdP."
            ).format(unsigned=", ".join(unsigned)),
        )
    if found:
        return (
            types.security.SecurityCheckSeverity.MEDIUM,
            True,
            _("All SAML authenticators require signed assertions or messages."),
        )
    return (
        types.security.SecurityCheckSeverity.MEDIUM,
        True,
        _("No SAML authenticators configured."),
    )


def _check_old_token_used_by_actor() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    # A user service is considered "still using the legacy flow" when its token
    # has never been rotated (AUTO_TOKEN_PREFIX_NOT_USED) AND the actor has
    # actually reported a version (i.e. an actor connected to it), which means it
    # is still authenticating with the old uuid-based token.
    legacy_actor_services = models.UserService.objects.filter(
        token__startswith=consts.auth.AUTO_TOKEN_PREFIX_NOT_USED,
        uuid__in=models.Properties.objects.filter(
            owner_type="userservice",
            key="actor_version",
        )
        .exclude(value="0.0.0")
        .values("owner_id"),
    )

    affected_pools = (
        models.ServicePool.objects.filter(userServices__in=legacy_actor_services).distinct().order_by("name")
    )

    if affected_pools:
        affected = ", ".join(pool.name for pool in affected_pools)
        return (
            types.security.SecurityCheckSeverity.MEDIUM,
            False,
            _(
                "Service pools with actors still using the legacy uuid token flow: {affected}."
                " Re-initialize affected actors to rotate them to the new token."
            ).format(affected=affected),
        )
    return (
        types.security.SecurityCheckSeverity.MEDIUM,
        True,
        _("No user services are using the legacy uuid actor token flow."),
    )


def _check_no_mfa_configured() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    authenticators = list(models.Authenticator.objects.all())
    if not authenticators:
        return (
            types.security.SecurityCheckSeverity.MEDIUM,
            True,
            _("No authenticators configured (nothing to enforce MFA on)."),
        )
    without_mfa = [a.name for a in authenticators if a.mfa is None]
    if len(without_mfa) == len(authenticators):
        return (
            types.security.SecurityCheckSeverity.MEDIUM,
            False,
            _("No authenticator has MFA configured: {names}. Assign at least one MFA per authenticator.").format(
                names=", ".join(without_mfa)
            ),
        )
    return (
        types.security.SecurityCheckSeverity.MEDIUM,
        True,
        _("MFA coverage: {with_mfa}/{total} authenticators have MFA assigned.").format(
            with_mfa=len(authenticators) - len(without_mfa),
            total=len(authenticators),
        ),
    )


def _check_server_certificates_expiring() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    # Imported here to avoid pulling cryptography into the check module's
    # import-time surface unless this check actually runs.
    import datetime as _dt
    from cryptography import x509 as _x509

    now = _dt.datetime.now(_dt.timezone.utc)
    horizon = now + _dt.timedelta(days=30)
    expired: list[str] = []
    expiring: list[str] = []

    qs = models.Server.objects.exclude(certificate="").exclude(certificate__isnull=True)
    for server in qs:
        cert_pem = server.certificate or ""
        if not cert_pem:
            continue
        # cryptography >= 42 returns not_valid_after_utc (tz-aware); older
        # versions return not_valid_after (naive). Handle both.
        try:
            cert = _x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
        except Exception:
            expired.append(f"{server.hostname or server.register_username or server.token[:8]} (unparseable)")
            continue
        try:
            expires_at = cert.not_valid_after_utc  # type: ignore[attr-defined]
        except AttributeError:
            expires_at = cert.not_valid_after
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=_dt.timezone.utc)
        label = server.hostname or server.register_username or server.token[:8]
        if expires_at < now:
            expired.append(f"{label} (expired {expires_at.date()})")
        elif expires_at < horizon:
            expiring.append(f"{label} (expires {expires_at.date()})")

    if expired:
        return (
            types.security.SecurityCheckSeverity.HIGH,
            False,
            _("{n} server certificate(s) have expired: {names}. Renew and rotate the affected servers.").format(
                n=len(expired), names=", ".join(expired)
            ),
        )
    if expiring:
        return (
            types.security.SecurityCheckSeverity.MEDIUM,
            False,
            _("{n} server certificate(s) expire within 30 days: {names}.").format(
                n=len(expiring), names=", ".join(expiring)
            ),
        )
    return (
        types.security.SecurityCheckSeverity.HIGH,
        True,
        _("No server certificates expire within the next 30 days."),
    )


def _check_restrained_service_pools() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    from uds.core.types.states import State

    # ServicePool.restraineds_queryset is the canonical source (already used
    # by ``/system/overview``). Restraint usually means repeated failures
    # in RESTRAINT_TIME, which can be a symptom of broken images or
    # tampering.
    restrained = models.ServicePool.restraineds_queryset().filter(state=State.RESTRAINED).values_list("name", flat=True)
    names = sorted(set(restrained))
    if names:
        return (
            types.security.SecurityCheckSeverity.INFO,
            False,
            _("{n} service pool(s) are currently restrained: {names}.").format(n=len(names), names=", ".join(names)),
        )
    return (
        types.security.SecurityCheckSeverity.INFO,
        True,
        _("No service pools are currently restrained."),
    )


def register_checks(factory: SecurityChecksFactory) -> None:
    """Registers the database-state checks into the shared factory."""
    factory.register_check("saml-assertions-signed", _check_saml_assertions_signed)
    factory.register_check("old-token-used-by-actor", _check_old_token_used_by_actor)
    factory.register_check("no-mfa-configured", _check_no_mfa_configured)
    factory.register_check("server-certificates-expiring", _check_server_certificates_expiring)
    factory.register_check("restrained-service-pools", _check_restrained_service_pools)
