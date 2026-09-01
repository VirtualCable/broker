"""Inventory of UDS REST handlers.

This module is the single source of truth for everything that needs to
know what a handler exposes: ``genapi`` for the OpenAPI spec, the MCP
catalog for ``list_*`` tools, the downloadable MCP skill for the
``SKILL.md`` description and any future code that wants to introspect
the broker surface.

The visitor is deterministic and is recomputed lazily. The cache lives
on a module-level ``lru_cache`` so that repeated calls during a request
are free but subclasses registered after the first call also show up
in subsequent visits.
"""

import collections.abc
import dataclasses
import functools
import inspect
import typing

from uds.REST.handlers import Handler
from uds.REST.model import detail as detail_model
from uds.REST.model import master as master_model
from uds.REST.model import base as model_base
from uds.REST.model.detail import DetailHandler
from uds.REST.model.master import ModelHandler
from uds.core import consts, types


@dataclasses.dataclass(frozen=True, slots=True)
class CustomMethodSummary:
    """One ``ModelCustomMethod`` exposed by a handler."""

    name: str
    method: types.rest.CustomMethodMethod
    needs_parent: bool
    description: str


@dataclasses.dataclass(frozen=True, slots=True)
class HandlerInventoryEntry:
    """A snapshot of a single REST handler's public surface."""

    handler: type[Handler]
    name: str
    path: str
    role: consts.Role | None
    is_collection: bool
    exposes_get_items: bool
    custom_methods: tuple[CustomMethodSummary, ...]
    api_operations: tuple[str, ...]
    description: str

    def supported_http_methods(self) -> frozenset[str]:
        """Return the HTTP methods that the handler implements."""
        methods: set[str] = set(self.api_operations)
        if "get" in (m.lower() for m in self.api_operations):
            methods.add("GET")
            methods.add("QUERY")
        if "post" in (m.lower() for m in self.api_operations):
            methods.add("POST")
        if "put" in (m.lower() for m in self.api_operations):
            methods.add("PUT")
        if "delete" in (m.lower() for m in self.api_operations):
            methods.add("DELETE")
        return frozenset(methods)


def _custom_methods(
    handler: type[Handler],
) -> tuple[CustomMethodSummary, ...]:
    custom = getattr(handler, "CUSTOM_METHODS", None) or ()
    return tuple(
        CustomMethodSummary(
            name=cm.name,
            method=cm.method,
            needs_parent=cm.needs_parent,
            description=cm.description or "",
        )
        for cm in custom
    )


def _api_operations(handler: type[Handler]) -> tuple[str, ...]:
    """Return the declared HTTP methods of the handler.

    The order is preserved so callers that emit specs see a stable
    sequence (``GET``, ``POST``, ``PUT``, ``DELETE``...).
    """
    declared: dict[str, None] = {}
    api_operations: typing.Any = getattr(handler, "API_OPERATIONS", None) or {}
    for method in api_operations:  # type: ignore[union-attr]
        declared[str(method).lower()] = None
    for method_name in ("get", "post", "put", "delete"):
        if hasattr(handler, method_name):
            declared.setdefault(method_name, None)
    return tuple(declared.keys())


def _description(handler: type[Handler]) -> str:
    info = getattr(handler, "REST_API_INFO", None)
    title = getattr(info, "title", None) if info is not None else None
    if title:
        return str(title)
    return handler.__name__


def _handler_name(handler: type[Handler]) -> str:
    name = getattr(handler, "NAME", None)
    if name:
        return str(name)
    return handler.__name__.lower()


def _handler_path(handler: type[Handler]) -> str:
    return getattr(handler, "PATH", None) or _handler_name(handler)


def _entry(handler: type[Handler]) -> HandlerInventoryEntry:
    is_collection = bool(getattr(handler, "get_items", None))
    return HandlerInventoryEntry(
        handler=handler,
        name=_handler_name(handler),
        path=_handler_path(handler),
        role=getattr(handler, "ROLE", None),
        is_collection=is_collection,
        exposes_get_items=is_collection,
        custom_methods=_custom_methods(handler),
        api_operations=_api_operations(handler),
        description=_description(handler),
    )


#: Methods a Master / Detail handler ``usually`` exposes on its own. The
#: visitor falls back to this list when the handler does not declare
#: ``API_OPERATIONS``.
_DEFAULT_OPERATIONS: typing.Final[tuple[str, ...]] = ("get", "post", "put", "delete")


def _handlers() -> list[type[Handler]]:
    """Return every concrete REST handler registered in the process.

    The visitor walks both the Master and the Detail subtrees, then
    falls back to scanning the module to pick any class that inherits
    directly from ``Handler`` (e.g. ``Login``).
    """
    seen: set[type[Handler]] = set()
    result: list[type[Handler]] = []
    queue: list[type[Handler]] = []
    queue.extend(ModelHandler.__subclasses__())  # type: ignore[var-annotated]
    queue.extend(DetailHandler.__subclasses__())  # type: ignore[var-annotated]
    queue.append(Handler)
    while queue:
        cls = queue.pop()
        if cls in seen or not inspect.isclass(cls) or cls is Handler:
            continue
        seen.add(cls)
        result.append(cls)
        queue.extend(cls.__subclasses__())  # type: ignore[var-annotated]
    return result


@functools.lru_cache(maxsize=1)
def walk_rest_handlers() -> tuple[HandlerInventoryEntry, ...]:
    """Return the cached inventory of REST handlers exposed by UDS.

    The inventory is computed once per process; callers that mutate the
    registry can call :func:`invalidate` to force a refresh.
    """
    entries: list[HandlerInventoryEntry] = []
    for cls in _handlers():
        # Skip the abstract base classes themselves; only real
        # ``MasterHandler`` and ``DetailHandler`` subclasses matter.
        if cls is model_base.BaseModelHandler or cls is master_model.ModelHandler or cls is detail_model.DetailHandler:
            continue
        entries.append(_entry(cls))
    entries.sort(key=lambda e: (e.path, e.name))
    return tuple(entries)


def collection_handlers() -> tuple[HandlerInventoryEntry, ...]:
    """Return the subset of handlers that expose a collection ``get_items()``.

    The MCP catalog uses this to generate ``list_*`` tools for every
    collection handler while keeping curated entries in charge of
    entries the operator wants to describe explicitly.
    """
    return tuple(entry for entry in walk_rest_handlers() if entry.is_collection)


def invalidate() -> None:
    """Drop the cached inventory (testing helper or hot-reload hook)."""
    walk_rest_handlers.cache_clear()


def tools_from_inventory(
    custom: collections.abc.Mapping[str, str] | None = None,
    exclude: collections.abc.Iterable[str] = (),
) -> list[HandlerInventoryEntry]:
    """Return the list of handlers that should become a ``list_*`` tool.

    The helper is just a filter: it skips curated entries that are already
    listed in ``custom`` and excludes handlers named in ``exclude``.
    """
    custom = custom or {}
    excluded = set(exclude)
    return [e for e in collection_handlers() if e.name not in custom and e.name not in excluded]
