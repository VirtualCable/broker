"""Deterministic fingerprints for REST items (mutability CAS support).

Mirrors the ETag semantics of the REST handlers
(``uds.core.types.rest.BaseRestItem.inmutables``), but iterates the field
set in sorted order: the fingerprint is persisted with the proposal and
compared days later, possibly by another worker, so it must be
repeatable across processes.
"""

import collections.abc
import hashlib
import typing


def _field_from_dict(data: dict[str, typing.Any], field: str) -> str:
    """Extract a field from a (possibly nested) item dict.

    Supports dot paths (``instance.name``, ``instance.cpu.low``, ...).
    """
    value: typing.Any = data
    for section in field.split("."):
        if isinstance(value, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            value = value.get(section) or ""  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

    return str(value)  # pyright: ignore[reportUnknownArgumentType]


def item_etag(item: dict[str, typing.Any], fields: collections.abc.Iterable[str]) -> str:
    """Stable sha256 over the sorted values of ``fields`` inside ``item``.

    Order of ``fields`` is irrelevant; duplicates are ignored.
    """
    canonical = "".join(_field_from_dict(item, f) for f in sorted(set(fields)))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
