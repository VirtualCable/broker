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

Checks derived from live database state (models).

Grouped here because they all inspect stored configuration rows
(authenticators, user services, ...) rather than ``django.conf.settings``
or the admin-editable global config.
"""

import typing

from django.db.models import Q
from django.utils.translation import gettext as _

from uds import models
from uds.core import consts, types
from uds.core.checks import Check, CheckOutcome


class SamlAssertionsSignedCheck(Check):
    """SAML authenticators not requiring signed assertions/messages."""

    id: typing.ClassVar[str] = "saml-assertions-signed"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY

    @typing.override
    def run(self) -> CheckOutcome:
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
                types.checks.CheckSeverity.MEDIUM,
                False,
                _(
                    "SAML assertions are not required to be signed on: {unsigned}."
                    " Unsigned assertions can be forged against a misconfigured IdP."
                ).format(unsigned=", ".join(unsigned)),
            )
        if found:
            return (
                types.checks.CheckSeverity.MEDIUM,
                True,
                _("All SAML authenticators require signed assertions or messages."),
            )
        return (
            types.checks.CheckSeverity.MEDIUM,
            True,
            _("No SAML authenticators configured."),
        )


class OldTokenUsedByActorCheck(Check):
    """User services still authenticating with the legacy uuid token flow."""

    id: typing.ClassVar[str] = "old-token-used-by-actor"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY

    @typing.override
    def run(self) -> CheckOutcome:
        # A user service is considered "still using the legacy flow" when its token
        # has never been rotated (INVALID_TOKEN_PREFIX) AND the actor has
        # actually reported a version (i.e. an actor connected to it), which means it
        # is still authenticating with the old uuid-based token.
        legacy_actor_services = models.UserService.objects.filter(
            uuid__in=models.Properties.objects.filter(
                owner_type="userservice",
                key="actor_version",
            )
            .exclude(value="0.0.0")
            .values("owner_id"),
        ).filter(Q(token_hash__startswith=consts.auth.INVALID_TOKEN_PREFIX))

        affected_pools = (
            models.ServicePool.objects.filter(userServices__in=legacy_actor_services)
            .distinct()
            .order_by("name")
        )

        if affected_pools:
            affected = ", ".join(pool.name for pool in affected_pools)
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _(
                    "Service pools with actors still using the legacy uuid token flow: {affected}."
                    " Re-initialize affected actors to rotate them to the new token."
                ).format(affected=affected),
            )
        return (
            types.checks.CheckSeverity.MEDIUM,
            True,
            _("No user services are using the legacy uuid actor token flow."),
        )


class NoMfaConfiguredCheck(Check):
    """Authenticators without MFA assigned."""

    id: typing.ClassVar[str] = "no-mfa-configured"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY

    @typing.override
    def run(self) -> CheckOutcome:
        authenticators = list(models.Authenticator.objects.all())
        if not authenticators:
            return (
                types.checks.CheckSeverity.MEDIUM,
                True,
                _("No authenticators configured (nothing to enforce MFA on)."),
            )
        without_mfa = [a.name for a in authenticators if a.mfa is None]
        if len(without_mfa) == len(authenticators):
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _(
                    "No authenticator has MFA configured: {names}. Assign at least one MFA per authenticator."
                ).format(names=", ".join(without_mfa)),
            )
        return (
            types.checks.CheckSeverity.MEDIUM,
            True,
            _("MFA coverage: {with_mfa}/{total} authenticators have MFA assigned.").format(
                with_mfa=len(authenticators) - len(without_mfa),
                total=len(authenticators),
            ),
        )


class ServerCertificatesExpiringCheck(Check):
    """Server certificates expired or expiring within 30 days."""

    id: typing.ClassVar[str] = "server-certificates-expiring"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY

    @typing.override
    def run(self) -> CheckOutcome:
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
                expired.append(
                    f"{server.hostname or server.register_username or server.properties.get('token_hint', server.uuid[:8])} (unparseable)"
                )
                continue
            try:
                expires_at = cert.not_valid_after_utc  # type: ignore[attr-defined]
            except AttributeError:
                expires_at = cert.not_valid_after
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=_dt.timezone.utc)
            label = (
                server.hostname
                or server.register_username
                or server.properties.get("token_hint", server.uuid[:8])
            )
            if expires_at < now:
                expired.append(f"{label} (expired {expires_at.date()})")
            elif expires_at < horizon:
                expiring.append(f"{label} (expires {expires_at.date()})")

        if expired:
            return (
                types.checks.CheckSeverity.HIGH,
                False,
                _(
                    "{n} server certificate(s) have expired: {names}. Renew and rotate the affected servers."
                ).format(n=len(expired), names=", ".join(expired)),
            )
        if expiring:
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _("{n} server certificate(s) expire within 30 days: {names}.").format(
                    n=len(expiring), names=", ".join(expiring)
                ),
            )
        return (
            types.checks.CheckSeverity.HIGH,
            True,
            _("No server certificates expire within the next 30 days."),
        )


class RestrainedServicePoolsCheck(Check):
    """Service pools currently in restrained state."""

    id: typing.ClassVar[str] = "restrained-service-pools"
    # Restraint is an operational symptom, not an exploitable weakness
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH

    @typing.override
    def run(self) -> CheckOutcome:
        from uds.core.types.states import State

        # ServicePool.restraineds_queryset is the canonical source (already used
        # by ``/system/overview``). Restraint usually means repeated failures
        # in RESTRAINT_TIME, which can be a symptom of broken images or
        # tampering.
        restrained = (
            models.ServicePool.restraineds_queryset()
            .filter(state=State.RESTRAINED)
            .values_list("name", flat=True)
        )
        names = sorted(set(restrained))
        if names:
            return (
                types.checks.CheckSeverity.INFO,
                False,
                _("{n} service pool(s) are currently restrained: {names}.").format(
                    n=len(names), names=", ".join(names)
                ),
            )
        return (
            types.checks.CheckSeverity.INFO,
            True,
            _("No service pools are currently restrained."),
        )
