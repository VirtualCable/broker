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
    uuid_property: JsonObject | None = None,
    extra_arg_properties: JsonObject | None = None,
    extra_properties: JsonObject | None = None,
    extra_required: tuple[str, ...] = (),
    access: str,
    returns: str,
    required_permission: str = "READ",
    sensitive_fields: tuple[str, ...] = (),
    path_args: tuple[str, ...] = ("uuid",),
    method_first: bool = False,
) -> ToolDefinition:
    """Build a tool around a GET custom method of a master handler.

    The URL is built from ``path``, the ordered arguments in ``path_args``,
    and ``custom_name`` (the method segment). With the default
    ``path_args=("uuid",)`` the URL is ``<path>/{uuid}/{custom_name}`` —
    the common case for ``needs_parent=True`` methods.

    For ``needs_parent=False`` methods the segment order flips: the method
    name comes first (``<path>/{custom_name}/...``) because the master
    dispatcher matches collection-scoped methods on ``_args[0]``. Pass
    ``method_first=True`` for those; with ``path_args=("item_id",)`` the
    URL becomes ``<path>/{custom_name}/{item_id}`` (e.g.
    ``Providers.service``, which reads the extra segment from
    ``self._args[1]``). With ``path_args=()`` both orders coincide
    (``<path>/{custom_name}``, e.g. ``Providers.allservices``).

    ``path_args`` doubles as the schema property list: every entry needs a
    matching schema in ``uuid_property`` (when it is ``"uuid"``) or
    ``extra_arg_properties``. Everything else travels to the handler as a
    query parameter, exactly like a direct REST call.

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

    arg_properties: JsonObject = {}
    if uuid_property is not None and "uuid" in path_args:
        arg_properties["uuid"] = uuid_property
    if extra_arg_properties:
        for arg_name, prop in extra_arg_properties.items():
            if arg_name in path_args:
                arg_properties[arg_name] = prop

    required = (*path_args, *extra_required)

    async def executor(arguments: JsonObject, request: typing.Any = None) -> typing.Any:
        check_required(arguments, required)
        arg_values = tuple(str(arguments[arg_name]) for arg_name in path_args)
        url_args = (custom_name, *arg_values) if method_first else (*arg_values, custom_name)
        params = {key: value for key, value in arguments.items() if key not in path_args}
        target = RestTarget(handler, path, GET, args=url_args)
        return await RestProxy().execute(target, request, params)

    properties: JsonObject = dict(arg_properties)
    if extra_properties:
        properties.update(extra_properties)

    return ToolDefinition(
        name=name,
        title=title,
        description=description,
        input_schema=schema(properties, required),
        access=access,
        returns=returns,
        required_permission=required_permission,
        sensitive_fields=sensitive_fields,
        executor=executor,
    )


def nested_custom_tool(
    *,
    name: str,
    title: str,
    description: str,
    handler: type[Handler],
    path: str,
    intermediate_name: str,
    custom_name: str,
    uuid_property: JsonObject,
    item_property: JsonObject,
    extra_properties: JsonObject | None = None,
    extra_required: tuple[str, ...] = (),
    access: str,
    returns: str,
    required_permission: str = "ALL",
    sensitive_fields: tuple[str, ...] = (),
) -> ToolDefinition:
    """Build a tool around a GET custom method of a detail handler item.

    URL: ``<path>/{uuid}/{intermediate_name}/{item_id}/{custom_name}``
    (e.g. ``authenticators/{uuid}/users/{item_id}/services_pools``).

    ``handler`` is the **master** handler that owns the detail collection
    (``Authenticators``, ``Providers``, ...), not the detail class: the
    call is routed exactly like the REST dispatcher does — the master is
    instantiated with the full argument tuple and its ``get()`` falls
    through to ``process_detail()``, which resolves the parent item by
    UUID, checks the parent access level and dispatches the custom method
    on the detail handler declared in the master's ``DETAIL`` mapping.

    Both the master UUID (``uuid``) and the detail item id (``item_id``)
    are path arguments and required inputs. Everything else travels to the
    handler as a query parameter, exactly like a direct REST call.

    ``required_permission`` defaults to ``"ALL"`` because most of the
    methods this helper covers are management-level (e.g.
    ``Users.services_pools``). It is exposed through ``_meta`` only;
    enforcement still happens on the REST side.
    """
    path_args = ("uuid", "item_id")
    required = (*path_args, *extra_required)

    async def executor(arguments: JsonObject, request: typing.Any = None) -> typing.Any:
        check_required(arguments, required)
        url_args = (
            str(arguments["uuid"]),
            intermediate_name,
            str(arguments["item_id"]),
            custom_name,
        )
        params = {key: value for key, value in arguments.items() if key not in path_args}
        target = RestTarget(handler, path, GET, args=url_args)
        return await RestProxy().execute(target, request, params)

    properties: JsonObject = {"uuid": uuid_property, "item_id": item_property}
    if extra_properties:
        properties.update(extra_properties)

    return ToolDefinition(
        name=name,
        title=title,
        description=description,
        input_schema=schema(properties, required),
        access=access,
        returns=returns,
        required_permission=required_permission,
        sensitive_fields=sensitive_fields,
        executor=executor,
    )
