"""Validation of MCP tool arguments against the published input schemas.

The MCP catalog emits a small, closed subset of JSON Schema: a top-level
object with typed properties (``string``, ``integer``, ``number``,
``boolean``, ``array``), ``items`` for arrays, ``minimum`` for numbers
and ``additionalProperties: false``. Validating exactly that subset here
turns argument mistakes into clean JSON-RPC ``-32602`` responses with
precise messages *before* any REST handler is touched, and avoids a
runtime dependency on a full JSON Schema library (``jsonschema`` is only
a transitive dependency in this project).

If the catalog ever emits richer schemas, extend this module or swap it
for a real validator; the call site in :class:`uds.mcp.MCPServerCore`
only relies on :class:`ValueError` being raised.
"""

import collections.abc
import typing

_TYPE_CHECKS: typing.Final[dict[str, collections.abc.Callable[[typing.Any], bool]]] = {
    "string": lambda v: isinstance(v, str),
    # bool is a subclass of int in Python; exclude it explicitly
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def _check_property(name: str, value: typing.Any, schema: dict[str, typing.Any]) -> str | None:
    """Return an error message for ``value`` against its property schema."""
    declared_type = schema.get("type")
    if isinstance(declared_type, str) and declared_type in _TYPE_CHECKS and not _TYPE_CHECKS[declared_type](value):
        return f"argument '{name}' must be of type {declared_type}"

    minimum = schema.get("minimum")
    if minimum is not None and isinstance(value, (int, float)) and not isinstance(value, bool) and value < minimum:
        return f"argument '{name}' must be >= {minimum}"

    maximum = schema.get("maximum")
    if maximum is not None and isinstance(value, (int, float)) and not isinstance(value, bool) and value > maximum:
        return f"argument '{name}' must be <= {maximum}"

    items_schema: typing.Any = schema.get("items")
    if isinstance(items_schema, dict) and isinstance(value, list):
        typed_items_schema = typing.cast("dict[str, typing.Any]", items_schema)
        item_type: typing.Any = typed_items_schema.get("type")
        if isinstance(item_type, str) and item_type in _TYPE_CHECKS:
            for index, item in enumerate(typing.cast("list[typing.Any]", value)):
                if not _TYPE_CHECKS[item_type](item):
                    return f"argument '{name}'[{index}] must be of type {item_type}"

    return None


def validate_arguments(schema: dict[str, typing.Any], arguments: typing.Any) -> None:
    """Validate tool ``arguments`` against the catalog's input ``schema``.

    Raises :class:`ValueError` with a concise, client-facing message on
    the first problem found: arguments that are not an object, unknown
    properties (the published schemas use ``additionalProperties:
    false``), values of the wrong type or below the declared minimum.
    """
    if not isinstance(arguments, dict):
        # ValueError (not TypeError) on purpose: the JSON-RPC layer maps
        # it to a clean ``-32602 invalid params`` response.
        raise ValueError("Tool arguments must be a JSON object")  # noqa: TRY004

    typed_arguments = typing.cast("dict[str, typing.Any]", arguments)
    properties: dict[str, typing.Any] = schema.get("properties") or {}
    additional_allowed = schema.get("additionalProperties", True)

    for name, value in typed_arguments.items():
        if name not in properties:
            if additional_allowed is False:
                allowed = ", ".join(sorted(properties)) or "none"
                raise ValueError(f"Unknown argument '{name}'; allowed arguments: {allowed}")
            continue
        error = _check_property(name, value, properties[name])
        if error is not None:
            raise ValueError(error)
