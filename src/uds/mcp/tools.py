"""Generic list-tool factory for the MCP catalog.

Built on top of the :mod:`uds.REST.inventory` walker so the same source
of truth feeds ``genapi``, the MCP catalog and the downloadable skill.
The factory reads the inventory and produces a paginated ``list_*`` tool
per model collection handler (``ModelHandler`` and ``DetailHandler``
descendants only; plain ``Handler`` collections are not published), with
a consistent OData input shape. Detail collections additionally require
the parent ``uuid`` argument so the tool can scope the query to a
concrete parent item.
"""

import collections.abc
import typing

from uds.REST.inventory import HandlerInventoryEntry, collection_handlers
from uds.REST.model.detail import DetailHandler
from uds.REST.model.master import ModelHandler

from .catalog import ToolDefinition
from .rest_proxy import RestProxy, RestTarget


def _humanize(token: str) -> str:
    """Convert a snake_case token to ``Title Case`` for tool titles."""
    parts = token.replace("_", " ").split()
    return " ".join(p.capitalize() for p in parts)


def _derive_tool_name(entry: HandlerInventoryEntry) -> str:
    """Return a stable, unique tool name for the inventory entry.

    The name is derived from the entry's full path so master and detail
    collections never collide (``authenticators/{uuid}/users`` becomes
    ``list_authenticators_users``, ``providers`` becomes ``list_providers``).
    """
    parts = [p for p in entry.path.split("/") if p not in ("{uuid}", "")]
    return "list_" + "_".join(parts)


def _list_input_schema(
    parent_desc: str | None = None,
) -> dict[str, typing.Any]:
    """Return the shared OData input schema for generated list tools.

    ``parent_desc`` adds the ``parent_uuid`` argument used to scope a
    detail collection to its parent item.
    """
    properties: dict[str, typing.Any] = {
        "filter": {
            "type": "string",
            "description": "OData $filter expression applied to the collection.",
        },
        "orderby": {
            "type": "string",
            "description": "OData $orderby expression applied to the collection.",
        },
        "top": {
            "type": "integer",
            "description": "Maximum number of items to return. Defaults to the REST collection cap.",
            "minimum": 1,
        },
        "skip": {
            "type": "integer",
            "description": "Number of items to skip before returning the page.",
            "minimum": 0,
        },
        "select": {
            "type": "array",
            "items": {"type": "string"},
            "description": "OData $select projection: list of property names to include.",
        },
    }
    if parent_desc is not None:
        properties["parent_uuid"] = {
            "type": "string",
            "description": parent_desc,
        }
    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }


def _generate_list_tool(entry: HandlerInventoryEntry) -> ToolDefinition | None:
    """Build a paginated list tool for a REST collection handler.

    Returns ``None`` when the inventory entry does not match the list
    pattern (for example, a handler that does not expose ``get_items``).
    Detail entries produce a tool whose target carries the parent target so
    the proxy can resolve the parent item and scope the query to it.
    """
    if not entry.exposes_get_items:
        return None
    tool_name = _derive_tool_name(entry)

    handler: type[ModelHandler[typing.Any] | DetailHandler[typing.Any]] = typing.cast(
        "type[ModelHandler[typing.Any] | DetailHandler[typing.Any]]", entry.handler
    )
    path = entry.path

    if entry.parent is not None:
        parent_handler: type[ModelHandler[typing.Any]] = typing.cast(
            "type[ModelHandler[typing.Any]]", entry.parent.handler
        )
        target = RestTarget(
            handler,
            path,
            parent=RestTarget(parent_handler, entry.parent.path),
        )
        parent_desc = (
            f"UUID of the parent {entry.parent.name} item this collection belongs to (e.g. {entry.parent.path})."
        )
    else:
        target = RestTarget(handler, path)
        parent_desc = None

    async def executor(
        arguments: dict[str, typing.Any],
        request: typing.Any = None,
    ) -> list[typing.Any]:
        return await RestProxy().execute_collection(target, request, arguments)

    schema = _list_input_schema(parent_desc)
    title_base = tool_name.removeprefix("list_")
    return ToolDefinition(
        name=tool_name,
        title=f"List {_humanize(title_base)}",
        description=(
            f"List items exposed by the ``{entry.handler.__name__}`` REST collection with "
            "optional OData-style filtering, ordering, pagination and projection."
            + (
                " The collection is scoped to the given parent item; the parent "
                f"is a ``{entry.parent.handler.__name__ if entry.parent else ''}``."
                if entry.parent is not None
                else ""
            )
        ),
        input_schema=schema,
        access=(f"Available to authenticated UDS users with permission to read {entry.handler.__name__} items."),
        returns=(f"An array of items from the ``{entry.handler.__name__}`` REST collection, each as a dictionary."),
        required_permission="READ",
        executor=executor,
    )


def generated_list_tools() -> collections.abc.Iterable[ToolDefinition]:
    """Yield a ``list_*`` tool for every model-shaped REST collection handler.

    Only ``ModelHandler`` and ``DetailHandler`` descendants are published;
    plain ``Handler`` collections are excluded so the MCP surface stays
    within the model CRUD/OData contract. Tool names are derived from the
    handler's full path, so master and detail collections never collide.
    """
    seen: set[str] = set()
    for entry in collection_handlers():
        if not issubclass(entry.handler, ModelHandler | DetailHandler):
            # Plain ``Handler`` collections (e.g. reports) are outside the
            # model CRUD/OData contract the generated tools rely on, so
            # they are never auto-published. Surfaces like reports will be
            # exposed later through hand-curated, purpose-specific tools
            # in the curated catalog, not through generic list tools.
            continue
        tool = _generate_list_tool(entry)
        if tool is None or tool.name in seen:
            continue
        seen.add(tool.name)
        yield tool
