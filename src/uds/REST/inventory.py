"""Walker over the real UDS REST handler tree.

This module is the single source of truth for anything that needs to know
what a handler exposes: ``genapi`` for the OpenAPI spec, the MCP catalog
for ``list_*`` tools, the downloadable MCP skill for the ``SKILL.md``
description and any future consumer that wants to introspect the broker
surface.

The walker visits :attr:`Dispatcher.root_node`, the tree built by the REST
dispatcher itself. Importing ``uds.REST`` triggers ``Dispatcher.initialize()``
(``dispatcher.py``), which dynamically imports every ``uds.REST.methods.*``
module and registers each concrete ``Handler`` subclass at its
``PATH``/``NAME`` location. There is no manual module scanning here: the
walker relies on that automatic registration, exactly like ``genapi`` does.

Consumers pass an ``accept`` predicate to :func:`walk_rest_handlers` to
select what they want. ``genapi`` accepts everything (it covers the whole
REST surface); the MCP catalog keeps collection-shaped handlers and curated
resources; future consumers can introduce their own filter without having to
change this module.

Detail handlers are not registered in the tree (``Dispatcher.initialize``
excludes them); they are discovered from ``ModelHandler.DETAIL`` and emitted
as entries whose path contains the ``{uuid}`` placeholder, mirroring how
``genapi`` documents them.
"""

import collections.abc
import dataclasses
import typing

from uds.REST.handlers import Handler
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
    """A snapshot of a single REST handler at a concrete tree location.

    ``path`` is the full REST path relative to the API root, without a
    leading slash (``"providers"``, ``"authenticators/{uuid}/users"``).
    Detail entries carry their parent entry in ``parent`` so consumers can
    build the parent-scoped call (the ``{uuid}`` argument).
    """

    handler: type[Handler]
    name: str
    path: str
    role: consts.Role | None
    is_collection: bool
    exposes_get_items: bool
    custom_methods: tuple[CustomMethodSummary, ...]
    api_operations: tuple[str, ...]
    description: str
    parent: "HandlerInventoryEntry | None" = None

    @property
    def is_detail(self) -> bool:
        """True when this entry represents a ``DetailHandler`` under a parent."""
        return self.parent is not None

    @property
    def full_path(self) -> str:
        """Return the path with the leading slash OpenAPI paths require."""
        return "/" + self.path.lstrip("/")

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


#: Callback the walker uses to decide which entries to keep.
Accept = collections.abc.Callable[[HandlerInventoryEntry], bool]


def _annotated_methods(handler: type[Handler]) -> tuple[str, ...]:
    """Return the declared HTTP methods of the handler.

    The order is preserved so callers that emit specs see a stable
    sequence (``GET``, ``POST``, ``PUT``, ``DELETE``...). The fallback
    heuristic probes for the four standard verbs so handlers that
    declare ``API_OPERATIONS`` empty still appear in the inventory.
    """
    declared: dict[str, None] = {}
    api_operations: typing.Any = getattr(handler, "API_OPERATIONS", None) or {}
    for method in api_operations:  # type: ignore[union-attr]
        declared[str(method).lower()] = None
    for method_name in ("get", "post", "put", "delete"):
        if hasattr(handler, method_name):
            declared.setdefault(method_name, None)
    return tuple(declared.keys())


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


def _is_collection(handler: type[Handler]) -> bool:
    return bool(getattr(handler, "get_items", None))


def _entry(
    handler: type[Handler],
    path: str,
    *,
    parent: "HandlerInventoryEntry | None" = None,
) -> HandlerInventoryEntry:
    is_collection = _is_collection(handler)
    return HandlerInventoryEntry(
        handler=handler,
        name=_handler_name(handler),
        path=path,
        role=getattr(handler, "ROLE", None),
        is_collection=is_collection,
        exposes_get_items=is_collection,
        custom_methods=_custom_methods(handler),
        api_operations=_annotated_methods(handler),
        description=_description(handler),
        parent=parent,
    )


def _walk(
    node: "types.rest.HandlerNode",
    emit: collections.abc.Callable[[HandlerInventoryEntry], None],
) -> None:
    """Visit one tree node, emitting its handler and simulated details.

    ``genapi`` documents detail handlers at ``{path}/{uuid}/{name}``; the
    walker mirrors that so every consumer sees the same REST surface. A
    detail handler is only emitted when its parent handler declares it in
    ``DETAIL``.
    """
    if node.handler is not None:
        path = node.full_path()
        entry = _entry(node.handler, path)
        emit(entry)

        # Only ``ModelHandler`` descendants declare details. ``getattr``
        # avoids the ``issubclass`` narrowing pyright cannot prove while
        # keeping the check safe for plain ``Handler`` nodes (System,
        # UDSVersion, Login, ...).
        details = getattr(node.handler, "DETAIL", None)
        if details:
            for detail_name, detail_cls in details.items():
                detail_path = f"{path}/{{uuid}}/{detail_name}"
                emit(_entry(detail_cls, detail_path, parent=entry))

    for child in node.children.values():
        _walk(child, emit)


def walk_rest_handlers(
    accept: Accept | None = None,
) -> tuple[HandlerInventoryEntry, ...]:
    """Return the REST handler inventory, optionally filtered by ``accept``.

    ``accept`` is an optional predicate applied to every
    :class:`HandlerInventoryEntry` produced by the walk. ``None`` accepts
    everything; a callable returning ``True`` keeps the entry and a ``False``
    drops it. The walk is deterministic and ordered by the dispatcher tree.
    """
    from uds.REST.dispatcher import Dispatcher  # import registers all handlers

    entries: list[HandlerInventoryEntry] = []

    def add(entry: HandlerInventoryEntry) -> None:
        if accept is None or accept(entry):
            entries.append(entry)

    _walk(Dispatcher.root_node, add)
    return tuple(entries)


def collection_handlers(
    accept: Accept | None = None,
) -> tuple[HandlerInventoryEntry, ...]:
    """Return the subset of entries that expose a collection ``get_items()``.

    ``accept`` filters first; ``get_items`` presence is always required.
    Used by the MCP catalog to generate ``list_*`` tools for every collection
    handler, including detail collections (which additionally require the
    parent ``{uuid}`` argument).
    """
    return tuple(entry for entry in walk_rest_handlers(accept) if entry.exposes_get_items)
