"""Shared building blocks for the curated tool modules.

Each domain module under :mod:`uds.mcp.curated` builds its tools from these
helpers so every curated tool shares the same schema conventions, argument
validation and proxy behaviour.
"""

import typing

from uds.core import types
from uds.REST.handlers import Handler

from ..catalog import ToolDefinition
from ..rest_proxy import RestProxy, RestTarget

GET = types.rest.CustomMethodMethod.GET
POST = types.rest.CustomMethodMethod.POST

JsonObject = dict[str, typing.Any]

# Upper bound for the CSV text a report tool may return. Reports are
# aggregations, but an unhappy configuration (or a very active broker) can
# still produce big documents; LLM clients pay per token, so the answer is
# clamped here with an explicit truncation marker.
MAX_REPORT_CHARS: typing.Final[int] = 65536


def schema(properties: JsonObject, required: tuple[str, ...] = ()) -> JsonObject:
    """Build an object input schema with the catalog's conventions."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def string_property(description: str) -> JsonObject:
    return {"type": "string", "description": description}


def uuid_property(description: str) -> JsonObject:
    return {"type": "string", "description": description}


def check_required(arguments: JsonObject, names: tuple[str, ...]) -> None:
    """Raise ``ValueError`` (surfaced as MCP ``invalid params``) on missing args.

    The JSON-Schema subset the server validates does not include
    ``required``, so the executor enforces it before touching the proxy.
    Empty or whitespace-only strings count as missing; other types only
    need to be present.
    """
    for name in names:
        value = arguments.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"{name} is required")


def master_custom_tool(
    *,
    name: str,
    title: str,
    description: str,
    handler: type[Handler],
    path: str,
    custom_name: str,
    uuid_property: JsonObject,
    extra_properties: JsonObject | None = None,
    extra_required: tuple[str, ...] = (),
    access: str,
    returns: str,
    required_permission: str = "READ",
    sensitive_fields: tuple[str, ...] = (),
) -> ToolDefinition:
    """Build a tool around a ``needs_parent`` GET custom method of a master handler.

    The URL arguments are known only at call time (they carry the target
    item's uuid), so the executor assembles the :class:`RestTarget` on every
    invocation. Everything in the arguments besides the uuid travels to the
    handler as query parameters, exactly like a direct REST call.

    ``required_permission`` reflects what the backing REST custom method
    actually requires (READ for most, ALL for service pools' ``stats``,
    ``actions_list``, ``list_assignables`` and similar). It is exposed through
    the tool ``_meta`` so clients reading the metadata see an honest answer;
    enforcement still happens on the REST side, exactly as for any other
    call routed through :class:`RestProxy`.

    ``sensitive_fields`` is unioned with the global denylist by
    :func:`uds.mcp.redaction.redact` so a tool whose handler returns
    personal data (IPs, friendly names, etc.) does not leak it through the
    MCP response.
    """

    async def executor(arguments: JsonObject, request: typing.Any = None) -> typing.Any:
        check_required(arguments, ("uuid", *extra_required))
        params = {key: value for key, value in arguments.items() if key != "uuid"}
        target = RestTarget(handler, path, GET, args=(str(arguments["uuid"]), custom_name))
        return await RestProxy().execute(target, request, params)

    properties: JsonObject = {"uuid": uuid_property}
    if extra_properties:
        properties.update(extra_properties)

    return ToolDefinition(
        name=name,
        title=title,
        description=description,
        input_schema=schema(properties, ("uuid", *extra_required)),
        access=access,
        returns=returns,
        required_permission=required_permission,
        sensitive_fields=sensitive_fields,
        executor=executor,
    )
