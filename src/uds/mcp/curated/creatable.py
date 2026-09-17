"""The creation gallery: which subtypes an agent can propose to create.

Creations depend on TYPES, not on objects (a provider is created out of
a provider type, a service out of a service type of its provider), so
the first step of any creation proposal is enumerating the available
subtypes. This tool exposes exactly the gallery the administration
interface shows, through the canonical ``GET {path}/types`` REST
surface (master handlers and detail handlers alike).
"""

import collections.abc
import typing

from uds.REST.methods.providers import Providers
from uds.REST.methods.services import Services
from uds.core.types.requests import ExtendedHttpRequestWithUser

from ..catalog import ToolDefinition
from ..rest_proxy import RestProxy, RestTarget
from .helpers import GET, check_required, schema, string_property, uuid_property

__all__ = ["curated_tools"]

JsonObject = dict[str, typing.Any]

_KIND_NEEDS_PARENT: typing.Final[dict[str, bool]] = {
    "provider": False,
    "service": True,
}

_KIND_TARGETS: typing.Final[dict[str, collections.abc.Callable[[str], RestTarget]]] = {
    "provider": lambda _parent: RestTarget(Providers, "providers", GET, args=("types",)),
    "service": lambda parent: RestTarget(
        Services,
        "providers/{uuid}/services",
        GET,
        args=("types",),
        parent=RestTarget(Providers, "providers"),
    ),
}


async def _executor(arguments: JsonObject, request: ExtendedHttpRequestWithUser | None = None) -> typing.Any:
    check_required(arguments, ("kind",))
    kind = str(arguments["kind"])
    builder = _KIND_TARGETS.get(kind)
    if builder is None:
        raise ValueError(f"Unknown kind: {kind} (expected one of {', '.join(sorted(_KIND_TARGETS))})")
    parent = str(arguments.get("parent_uuid", "") or "")
    if _KIND_NEEDS_PARENT[kind]:
        check_required(arguments, ("parent_uuid",))
    return await RestProxy().execute(builder(parent), request, {}, parent or None)


def curated_tools() -> tuple[ToolDefinition, ...]:
    """Return the creation gallery tool."""
    return (
        ToolDefinition(
            name="get_creatable_types",
            title="List creatable subtypes",
            description=(
                "List the subtypes available for creation proposals of one kind: the same "
                "gallery the administration interface shows. Creations depend on types, not "
                "on objects: propose one with propose_{kind}_create, passing its type as "
                "data_type and the initial field values (discover them with get_mutable_fields "
                "and this type as for_type). Kinds with a parent (service of a provider) need "
                "the parent uuid: the available subtypes depend on it."
            ),
            input_schema=schema(
                {
                    "kind": string_property(
                        "Kind of entity to create (provider, service, ...).",
                    ),
                    "parent_uuid": uuid_property(
                        "UUID of the parent item, for kinds created inside a container "
                        "(e.g. service needs the provider).",
                    ),
                },
                ("kind",),
            ),
            access="Available to authenticated UDS users with read permission on the kind's collection.",
            returns="An array of subtype descriptors (type, name, description, icon, group).",
            read_only=True,
            executor=_executor,
        ),
    )
