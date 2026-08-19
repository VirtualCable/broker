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

Security checks derived from ``django.conf.settings``.

Grouped here because their state is fixed at process start (the settings file)
and is the same across every request: cookies, headers, enhanced-security flag.
"""

from django.conf import settings
from django.utils.translation import gettext as _

from uds.core import consts, types
from uds.core.util.config import GlobalConfig

from .factory import SecurityChecksFactory


def _check_security_cookies_and_headers() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
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
            _("Security cookies/headers are disabled: {missing}.").format(missing=missing),
        )
    return (
        types.security.SecurityCheckSeverity.LOW,
        True,
        _("Session/security cookies and enhanced security are enabled."),
    )


def _check_debug_enabled() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    problems: list[str] = []
    if settings.DEBUG:
        problems.append("DEBUG=True")
    if getattr(settings, "PROFILING", False):
        problems.append("PROFILING=True")
    if problems:
        return (
            types.security.SecurityCheckSeverity.CRITICAL,
            False,
            _("Production debug switches are on: {problems}. Disable both in production.").format(
                problems=", ".join(problems)
            ),
        )
    return (
        types.security.SecurityCheckSeverity.CRITICAL,
        True,
        _("DEBUG and PROFILING are both off."),
    )


def _check_default_secret_key() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    if str(getattr(settings, "SECRET_KEY", "")) == consts.security.DEFAULT_SECRET_KEY:
        return (
            types.security.SecurityCheckSeverity.CRITICAL,
            False,
            _("settings.SECRET_KEY is the shipped sample value: session tokens can be forged."),
        )
    return (
        types.security.SecurityCheckSeverity.CRITICAL,
        True,
        _("settings.SECRET_KEY has been rotated."),
    )


def _check_default_rsa_key() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    if (
        consts.security.rsa_key_fingerprint(str(getattr(settings, "RSA_KEY", "")))
        == consts.security.DEFAULT_RSA_KEY_SHA256
    ):
        return (
            types.security.SecurityCheckSeverity.CRITICAL,
            False,
            _("settings.RSA_KEY is the shipped sample value: anyone can decrypt broker secrets."),
        )
    return (
        types.security.SecurityCheckSeverity.CRITICAL,
        True,
        _("settings.RSA_KEY has been rotated."),
    )


def _check_csrf_middleware_disabled() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    middleware: list[str] = list(getattr(settings, "MIDDLEWARE", []) or [])
    if "django.middleware.csrf.CsrfViewMiddleware" not in middleware:
        return (
            types.security.SecurityCheckSeverity.HIGH,
            False,
            _(
                "django.middleware.csrf.CsrfViewMiddleware is not in MIDDLEWARE: cross-site request"
                " forgery is not mitigated at the framework level."
            ),
        )
    return (
        types.security.SecurityCheckSeverity.HIGH,
        True,
        _("django.middleware.csrf.CsrfViewMiddleware is active."),
    )


def _check_sql_logging_enabled() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    # Only flagged in production (DEBUG=False). In dev the SQL log is expected
    # noise; in prod every bound parameter (passwords, personal data) ends up
    # on disk forever, which is a sensitive-data-at-rest leak.
    loggers = getattr(settings, "LOGGING", {}).get("loggers", {}) or {}
    db_logger = dict(loggers.get("django.db.backends", {}) or {})
    level = str(db_logger.get("level", "")).upper() or "WARNING"
    if level == "DEBUG" and not settings.DEBUG:
        return (
            types.security.SecurityCheckSeverity.MEDIUM,
            False,
            _(
                "SQL logger 'django.db.backends' is at DEBUG while DEBUG is off: every SQL"
                " statement with bound values is being written to log/sql.log on disk."
            ),
        )
    return (
        types.security.SecurityCheckSeverity.MEDIUM,
        True,
        _("SQL logger 'django.db.backends' is not writing bound SQL to disk in production."),
    )


def _check_log_level_debug() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    # If A1 already fires, the root logger is also DEBUG via LOGLEVEL. If
    # A1 does not fire (DEBUG=False in production) but the root/uds logger
    # is still at DEBUG, we are noisier than necessary.
    root_loggers = getattr(settings, "LOGGING", {}).get("loggers", {}) or {}
    root_level = str(root_loggers.get("", {}).get("level", "")).upper()
    uds_level = str(root_loggers.get("uds", {}).get("level", "")).upper()
    if root_level == "DEBUG" or uds_level == "DEBUG":
        return (
            types.security.SecurityCheckSeverity.LOW,
            False,
            _("Root or 'uds' logger is at DEBUG in production (verbose logs on disk)."),
        )
    return (
        types.security.SecurityCheckSeverity.LOW,
        True,
        _("Root and 'uds' loggers are above DEBUG in production."),
    )


def _check_no_email_backend() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    # Detects the "no EMAIL_* configured" case: ``mail_admins`` and any
    # email-based notification silently fail to localhost:25.
    email_backend = getattr(settings, "EMAIL_BACKEND", None)
    if email_backend is None or str(email_backend).strip() == "":
        return (
            types.security.SecurityCheckSeverity.INFO,
            False,
            _(
                "EMAIL_BACKEND is not configured: 'mail_admins' and email notifications"
                " silently fail. Operators will not receive ERROR-level alerts by mail."
            ),
        )
    return (
        types.security.SecurityCheckSeverity.INFO,
        True,
        _("EMAIL_BACKEND is configured."),
    )


def _check_memcached_unauthenticated() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    caches = getattr(settings, "CACHES", {}) or {}
    mem = dict(caches.get("memory", {}) or {})
    location = str(mem.get("LOCATION", ""))
    if "127.0.0.1" not in location and "localhost" not in location and location != "":
        return (
            types.security.SecurityCheckSeverity.INFO,
            False,
            _(
                "CACHES['memory'] LOCATION={location!r} is reachable from outside the host:"
                " any local user can read or poison the cache unless a SASL/TLS"
                " username is configured."
            ).format(location=location),
        )
    return (
        types.security.SecurityCheckSeverity.INFO,
        True,
        _("CACHES['memory'] is bound to localhost (or absent)."),
    )


def _check_hsts_not_enforced() -> tuple[types.security.SecurityCheckSeverity, bool, str]:
    # HSTS is added by UDSSecurityMiddleware only when request.is_secure()
    # (i.e. behind HTTPS). If the broker is served behind HTTP only, HSTS
    # never fires regardless of settings. The settings-level knob we can
    # observe is SECURE_HSTS_SECONDS: if it's not set AND the broker
    # appears to be HTTPS-fronted, flag it.
    hsts_seconds = getattr(settings, "SECURE_HSTS_SECONDS", 0)
    secure_proxy_header = getattr(settings, "SECURE_PROXY_SSL_HEADER", None)
    if hsts_seconds:
        return (
            types.security.SecurityCheckSeverity.INFO,
            True,
            _("SECURE_HSTS_SECONDS is set ({seconds}s).").format(seconds=hsts_seconds),
        )
    if secure_proxy_header:
        return (
            types.security.SecurityCheckSeverity.INFO,
            False,
            _(
                "Broker is HTTPS-fronted (SECURE_PROXY_SSL_HEADER) but SECURE_HSTS_SECONDS is 0:"
                " no HSTS header is emitted on HTTPS responses."
            ),
        )
    return (
        types.security.SecurityCheckSeverity.INFO,
        True,
        _("HSTS is not configured; broker does not appear to be HTTPS-fronted."),
    )


def register_checks(factory: SecurityChecksFactory) -> None:
    """Registers the settings-derived checks into the shared factory."""
    factory.register_check("security-cookies-and-headers", _check_security_cookies_and_headers)
    factory.register_check("debug-enabled", _check_debug_enabled)
    factory.register_check("default-secret-key", _check_default_secret_key)
    factory.register_check("default-rsa-key", _check_default_rsa_key)
    factory.register_check("csrf-middleware-disabled", _check_csrf_middleware_disabled)
    factory.register_check("sql-logging-enabled", _check_sql_logging_enabled)
    factory.register_check("log-level-debug", _check_log_level_debug)
    factory.register_check("no-email-backend", _check_no_email_backend)
    factory.register_check("memcached-unauthenticated", _check_memcached_unauthenticated)
    factory.register_check("hsts-not-enforced", _check_hsts_not_enforced)
