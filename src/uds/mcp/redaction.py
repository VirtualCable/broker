"""Defensive redaction for MCP responses and metadata."""

import collections.abc
import functools
import typing

from uds.core.consts.rest import REDACTED

# Re-exported for convenience: the marker lives in core so both the REST
# layer (``$redacted``) and MCP use the exact same value.
__all__ = ["REDACTED", "SENSITIVE_FIELDS", "module_sensitive_fields", "redact"]

SENSITIVE_FIELDS: typing.Final[frozenset[str]] = frozenset(
    {
        # Existing names, kept as the baseline.
        "password",
        "passwd",
        "token",
        "token_hash",
        "secret",
        "private_key",
        "access_token",
        "refresh_token",
        "api_key",
        "credential",
        # Wider secret-name coverage so every key an item can realistically
        # carry is caught (denylist; see plan: allowlists per curated tool).
        "pwd",
        "pass",
        "passphrase",
        "secret_key",
        "client_secret",
        "api_secret",
        "access_key",
        "auth_key",
        "credentials",
        "private",
        "cookie",
        "cookies",
        "sessionid",
        "csrf",
        "csrfmiddlewaretoken",
        "cert",
        "certificate",
        "otp",
        "seed",
        "user_token",
        "service_token",
    }
)


def _redact_with(value: typing.Any, sensitive: frozenset[str]) -> typing.Any:
    """Recursive worker that already has the unioned sensitive-key set."""
    if isinstance(value, collections.abc.Mapping):
        mapping = typing.cast(collections.abc.Mapping[str, typing.Any], value)
        return {
            key: REDACTED if key.lower() in sensitive else _redact_with(item, sensitive)
            for key, item in mapping.items()
        }
    if isinstance(value, list):
        return [_redact_with(item, sensitive) for item in typing.cast(list[typing.Any], value)]
    if isinstance(value, tuple):
        return tuple(_redact_with(item, sensitive) for item in typing.cast(tuple[typing.Any, ...], value))
    return value


def redact(value: typing.Any, extra_keys: collections.abc.Iterable[typing.Any] = ()) -> typing.Any:
    """Return a recursively redacted copy of an MCP-compatible value.

    Key names are matched case-insensitively against the union of
    :data:`SENSITIVE_FIELDS` (the global denylist) and ``extra_keys``. The
    ``extra_keys`` argument lets a curated tool or resource declare fields
    that are sensitive *for its own output* without widening the global
    denylist; when no curated declaration sets any, the union degenerates
    to the global list, so the behaviour is preserved for today's tools.
    Non-string entries in ``extra_keys`` are silently ignored as a defence
    against malformed declarations: the function's contract is "string names".
    """
    extras = frozenset(key.lower() for key in extra_keys if isinstance(key, str))
    sensitive = SENSITIVE_FIELDS | extras
    return _redact_with(value, sensitive)


@functools.lru_cache(maxsize=1)
def module_sensitive_fields() -> frozenset[str]:
    """Union of the sensitive field names declared by every registered module.

    Every ``Module`` exposes its own set through
    ``UserInterface.get_sensitive_fields()`` (its declared
    ``gui.PasswordField`` names plus any explicit ``sensitive_fields``).
    MCP redacts with the union of this set and the global
    :data:`SENSITIVE_FIELDS`, so secrets declared by any module (even with
    names outside the global denylist) are never exposed, whatever the
    tool that returns them.
    """
    # Imported here to avoid import cycles at module load time
    from uds.core import auths, mfas, messaging, osmanagers, reports, services, transports
    from uds.core.ui.user_interface import UserInterface

    names: set[str] = set()
    factories: tuple[typing.Any, ...] = (
        services.factory(),
        transports.factory(),
        osmanagers.factory(),
        auths.factory(),
        mfas.factory(),
        messaging.factory(),
        reports.factory(),
    )
    for factory in factories:
        module_types: list[type] = list(factory.objects().values())
        for module_type in module_types:
            # Service modules hang from their providers ("offers")
            if hasattr(module_type, "offers"):
                module_types.extend(module_type.offers)  # type: ignore[attr-defined]
            if issubclass(module_type, UserInterface):
                names |= module_type.get_sensitive_fields()
    return frozenset(names)
