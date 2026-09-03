"""Defensive redaction for MCP responses and metadata."""

import collections.abc
import typing


REDACTED = "REDACTED"

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


def redact(value: typing.Any) -> typing.Any:
    """Return a recursively redacted copy of an MCP-compatible value."""
    if isinstance(value, collections.abc.Mapping):
        mapping = typing.cast(collections.abc.Mapping[str, typing.Any], value)
        return {key: REDACTED if key.lower() in SENSITIVE_FIELDS else redact(item) for key, item in mapping.items()}
    if isinstance(value, list):
        return [redact(item) for item in typing.cast(list[typing.Any], value)]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in typing.cast(tuple[typing.Any, ...], value))
    return value
