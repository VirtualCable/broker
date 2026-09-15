"""Generated read tools for the descriptor families of the mutability registry.

The read side of an entity is not an action: it lives on
:class:`~uds.mutability.base.EntityDescriptor.read` and is published here,
as part of the always-active curated surface (reads need no mutation
gate). One ``get_<root>`` tool per readable family — the family declares
it publishes by overriding ``read`` (implementation is declaration, the
same rule the write registry follows with its ``op_*`` hooks).

The executors call the descriptor directly (synchronously, no flow, no
approval), inside a ``sync_to_async`` boundary because Django ORM access
is not allowed from the MCP async event loop.
"""

import collections.abc
import typing

from asgiref.sync import sync_to_async

from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.mutability import registry
from uds.mutability.base import JsonObject

from ..catalog import ToolDefinition

__all__ = ["curated_tools"]

ExecutorSync = collections.abc.Callable[[JsonObject, ExtendedHttpRequestWithUser], typing.Any]


def _wrap_sync(sync_body: ExecutorSync) -> collections.abc.Callable[..., typing.Any]:
    """Executor wrapper: run the sync body off the event loop (request mandatory)."""

    async def executor(arguments: JsonObject, request: ExtendedHttpRequestWithUser | None = None) -> typing.Any:
        if request is None:
            raise rest_exceptions.AccessDenied()
        return await sync_to_async(sync_body, thread_sensitive=True)(arguments, request)

    return executor


def _read_tool(family: type[registry.MutableActionType]) -> ToolDefinition:
    """Build the read tool of one descriptor family."""
    descriptor = family()

    def sync_body(arguments: JsonObject, request: ExtendedHttpRequestWithUser) -> JsonObject:
        user: typing.Any = getattr(request, "user", None)
        if user is None or not user.uuid:
            raise rest_exceptions.AccessDenied()
        target_uuid = str(arguments.get("target_uuid", "") or "")
        if not target_uuid.strip():
            raise ValueError("target_uuid is required")
        return descriptor.read(target_uuid)

    root = family.type_id.replace(".", "_")
    return ToolDefinition(
        name=f"get_{root}",
        title=descriptor.read_title(),
        description=descriptor.read_description(),
        input_schema={
            "type": "object",
            "properties": {
                "target_uuid": {
                    "type": "string",
                    "description": f"UUID of the {descriptor.noun.lower()} to read.",
                },
            },
            "required": ["target_uuid"],
            "additionalProperties": False,
        },
        access="Staff (the MCP endpoint gate).",
        returns="The target's field definitions, current values, and related members where applicable.",
        read_only=True,
        executor=_wrap_sync(sync_body),
    )


def curated_tools() -> tuple[ToolDefinition, ...]:
    """Return one read tool per readable descriptor family."""
    return tuple(_read_tool(family) for family in registry.all_families() if family.readable())
