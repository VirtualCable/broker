"""Generic list-tool factory for the MCP catalog.

Mirrors the static-analysis approach used by ``genapi`` so the same
data source that emits the OpenAPI spec can also emit MCP tools and
resources. The factory walks the REST handler graph, finds the
collection-shaped handlers, and produces a paginated ``list_<name>``
tool per handler, with a consistent OData input shape.

The goal is to add MCP support for an entire REST collection without
writing a new entry by hand. The first implementation only covers the
shapes we already have in the curated catalog: a single ``ModelHandler``
that exposes ``get_items()`` and a Django model.
"""

import collections.abc
import typing

from uds.REST.methods.services_pools import ServicesPools
from uds.REST.model.master import ModelHandler

from .catalog import ToolDefinition
from .rest_proxy import ODataArgs, RestTarget, RestProxy


# Tools which already have a custom MCP executor do not need a generated
# entry. The exclusion is by exact tool name to keep the surface
# predictable and to avoid duplicates.
_GENERATED_TOOL_EXCLUSIONS: typing.Final[frozenset[str]] = frozenset(
    {
        # ``list_service_pools`` is generated explicitly in the curated
        # catalog so we keep the curated version (with custom access
        # description) instead of the generic default.
        "list_service_pools",
    }
)


def _humanize(token: str) -> str:
    """Convert a snake_case token to ``Title Case`` for tool titles."""
    parts = token.replace("_", " ").split()
    return " ".join(p.capitalize() for p in parts)


def pluralize(token: str) -> str:
    """Return the English plural of a snake_case noun.

    The implementation is intentionally simple and matches the
    naming conventions used by UDS REST collections. It is not a
    general-purpose inflector; if a name is irregular, add it to the
    handler's own ``display_plural`` annotation instead of relying on
    this heuristic.
    """
    if not token:
        return token
    # Irregular cases.
    irregular: dict[str, str] = {}
    if token in irregular:
        return irregular[token]
    if token.endswith("y") and len(token) > 1 and token[-2] not in "aeiou":
        return token[:-1] + "ies"
    if token.endswith("s"):
        return token
    if token.endswith(("sh", "ch", "x")):
        return token + "es"
    return token + "s"


def _generate_list_tool(
    handler: type[ModelHandler[typing.Any]],
) -> ToolDefinition | None:
    """Build a paginated list tool for a REST collection handler.

    Returns ``None`` when the handler does not match the list pattern.
    """
    if not hasattr(handler, "get_items"):
        return None
    name = handler.__name__.lower()
    if not name.endswith("s"):
        name = pluralize(name)
    if not name.startswith("list_"):
        tool_name = f"list_{name}"
    else:
        tool_name = name
    if tool_name in _GENERATED_TOOL_EXCLUSIONS:
        return None

    path = getattr(handler, "PATH", None) or getattr(handler, "NAME", None) or handler.__name__.lower()

    async def executor(
        arguments: dict[str, typing.Any],
    ) -> list[typing.Any]:
        proxy = RestProxy()
        args = ODataArgs(
            filter=typing.cast("str | None", arguments.get("filter")),
            orderby=typing.cast("str | None", arguments.get("orderby")),
            top=typing.cast("int | None", arguments.get("top")),
            skip=typing.cast("int | None", arguments.get("skip")),
            select=tuple(typing.cast("list[str]", arguments.get("select", []) or [])),
        )
        return await proxy.execute_collection(RestTarget(handler, path), None, args)

    # Expose the handler class and the resolved REST path so callers
    # that want to bind a live request (like ``default_catalog_for_request``)
    # can rebuild the same ``RestTarget`` without parsing the tool name.
    executor._uds_handler_class = handler  # type: ignore[attr-defined]
    executor._uds_handler_path = path  # type: ignore[attr-defined]

    schema = _list_input_schema()
    return ToolDefinition(
        name=tool_name,
        title=f"List {_humanize(name.removeprefix('list_').removesuffix('s'))}s",
        description=(
            f"List items exposed by the ``{handler.__name__}`` REST collection with "
            "optional OData-style filtering, ordering, pagination and projection."
        ),
        input_schema=schema,
        access=(f"Available to authenticated UDS users with permission to read {handler.__name__} items."),
        returns=(f"An array of items from the ``{handler.__name__}`` REST collection, each as a dictionary."),
        required_permission="READ",
        executor=executor,
    )


def _list_input_schema() -> dict[str, typing.Any]:
    """Return the shared OData input schema for generated list tools."""
    return {
        "type": "object",
        "properties": {
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
        },
        "additionalProperties": False,
    }


def generated_list_tools() -> collections.abc.Iterable[ToolDefinition]:
    """Yield list tools for every collection-shaped REST handler in the catalog.

    Handlers that are listed in :data:`_GENERATED_TOOL_EXCLUSIONS` are
    skipped so curated entries always win. The iteration is deterministic
    by handler class name, which makes the generated catalog stable.
    """
    seen: set[str] = set()
    candidates: list[type[ModelHandler[typing.Any]]] = []
    for cls_any in ModelHandler.__subclasses__():  # type: ignore[var-annotated]
        cls: type[ModelHandler[typing.Any]] = typing.cast("type[ModelHandler[typing.Any]]", cls_any)
        if cls is ServicesPools:
            continue
        if hasattr(cls, "get_items"):
            candidates.append(cls)
    candidates.sort(key=lambda c: c.__name__)
    for cls in candidates:
        tool = _generate_list_tool(cls)  # type: ignore[arg-type]
        if tool is None or tool.name in seen:
            continue
        seen.add(tool.name)
        yield tool
