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
Author: Andres Schumann, aschumann at virtualcable dot es

Checks derived from live database state (models).

Grouped here because they all inspect stored configuration rows
(authenticators, user services, ...) rather than ``django.conf.settings``
or the admin-editable global config.
"""

import typing

from django.db.models import Q
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from uds import models
from uds.core import consts, types
from uds.core.checks import AutomaticCheck, CheckOutcome


class SamlAssertionsSignedCheck(AutomaticCheck):
    """SAML authenticators not requiring signed assertions/messages."""

    id: typing.ClassVar[str] = "saml-assertions-signed"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY
    description: typing.ClassVar[str] = gettext_noop(
        "Verifies that every SAML authenticator requires signed assertions or signed messages. "
        "Without a signature, a forged response could be accepted if the IdP is misconfigured. "
        "Enable one of the two options in the listed authenticators."
    )

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
                unsigned,
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


class OldTokenUsedByActorCheck(AutomaticCheck):
    """User services still authenticating with the legacy uuid token flow."""

    id: typing.ClassVar[str] = "old-token-used-by-actor"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY
    description: typing.ClassVar[str] = gettext_noop(
        "Looks for machines whose actor still authenticates with the old token, the one based on "
        "the user service id. Re-initialize the actor on the machines of the listed service pools "
        "so they move to the new token."
    )

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
                [pool.name for pool in affected_pools],
            )
        return (
            types.checks.CheckSeverity.MEDIUM,
            True,
            _("No user services are using the legacy uuid actor token flow."),
        )


class NoMfaConfiguredCheck(AutomaticCheck):
    """Authenticators without MFA assigned."""

    id: typing.ClassVar[str] = "no-mfa-configured"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY
    description: typing.ClassVar[str] = gettext_noop(
        "Verifies that at least one authenticator has multi-factor authentication (MFA) assigned; "
        "it only fails when none has. The details list the authenticators without MFA. Assign an "
        "MFA to the authenticators your users log in with."
    )

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
                without_mfa,
            )
        return (
            types.checks.CheckSeverity.MEDIUM,
            True,
            _("MFA coverage: {with_mfa}/{total} authenticators have MFA assigned.").format(
                with_mfa=len(authenticators) - len(without_mfa),
                total=len(authenticators),
            ),
            without_mfa,
        )


class ServerCertificatesExpiringCheck(AutomaticCheck):
    """Server certificates expired or expiring within 30 days."""

    id: typing.ClassVar[str] = "server-certificates-expiring"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY
    description: typing.ClassVar[str] = gettext_noop(
        "Reads the certificates of the registered servers and warns when one has expired, expires "
        "within 30 days or cannot be read. Connections to a server with an expired certificate "
        "fail. Renew the certificate of the listed servers."
    )

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
                expired + expiring,
            )
        if expiring:
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _("{n} server certificate(s) expire within 30 days: {names}.").format(
                    n=len(expiring), names=", ".join(expiring)
                ),
                expiring,
            )
        return (
            types.checks.CheckSeverity.HIGH,
            True,
            _("No server certificates expire within the next 30 days."),
        )


class RestrainedServicePoolsCheck(AutomaticCheck):
    """Service pools currently in restrained state."""

    id: typing.ClassVar[str] = "restrained-service-pools"
    # Restraint is an operational symptom, not an exploitable weakness
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH
    description: typing.ClassVar[str] = gettext_noop(
        "Lists the service pools that UDS has restrained because too many of their machines "
        "failed in a short time. A restrained pool stops creating machines. Check the logs of the "
        "pool and its provider to find the cause."
    )

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
                names,
            )
        return (
            types.checks.CheckSeverity.INFO,
            True,
            _("No service pools are currently restrained."),
        )


class AuthenticatorSslVerificationDisabledCheck(AutomaticCheck):
    """Authenticators that talk TLS without verifying the certificate of the other end."""

    id: typing.ClassVar[str] = "authenticator-ssl-verification-disabled"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.SECURITY
    description: typing.ClassVar[str] = gettext_noop(
        "Looks for LDAP authenticators that use SSL without verifying the server certificate, and "
        "SAML authenticators that download the IdP metadata over HTTPS without verifying it. "
        "Anyone able to intercept that traffic can impersonate the directory or the identity "
        "provider. Enable the verification and install the CA if it is an internal one."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        # Imported here to avoid loading authenticator modules (and their optional
        # dependencies) unless this check actually runs.
        from uds.auths.RegexLdap import RegexLdap
        from uds.auths.SAML import SAMLAuthenticator
        from uds.auths.SimpleLDAP import SimpleLDAPAuthenticator

        unverified: list[str] = []
        for authenticator in models.Authenticator.objects.all():
            auth_type = authenticator.get_type()
            if auth_type in (SimpleLDAPAuthenticator, RegexLdap):
                ldap = typing.cast("SimpleLDAPAuthenticator | RegexLdap", authenticator.get_instance())
                if ldap.use_ssl.as_bool() and not ldap.verify_ssl.as_bool():
                    unverified.append(authenticator.name)
            elif auth_type is SAMLAuthenticator:
                saml = typing.cast("SAMLAuthenticator", authenticator.get_instance())
                if (
                    saml.idp_metadata.value.startswith("https://")
                    and not saml.check_https_certificate.as_bool()
                ):
                    unverified.append(authenticator.name)

        if unverified:
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _(
                    "TLS certificate verification is disabled on: {names}."
                    " Anyone able to intercept the traffic can impersonate the directory or the identity provider."
                    " Enable the verification and install the CA if it is an internal one."
                ).format(names=", ".join(unverified)),
                unverified,
            )
        return (
            types.checks.CheckSeverity.MEDIUM,
            True,
            _("All authenticators using TLS verify the certificate of the other end."),
        )
