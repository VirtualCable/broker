"""Defensive redaction for MCP responses and metadata."""

import collections.abc
import typing


REDACTED = "REDACTED"

SENSITIVE_FIELDS: typing.Final[frozenset[str]] = frozenset(
    {
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
