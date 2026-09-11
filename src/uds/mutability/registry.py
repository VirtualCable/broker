"""Code-defined registry of mutable action types.

The registry is populated at import time from the ``types_*.py``
modules: an agent cannot invent action types or mutate anything outside
this surface — the mutability frontier is reviewed in code review, not
configurable at runtime.
"""

from .base import JsonObject, MutableActionType

_REGISTRY: dict[str, MutableActionType] = {}


def register(action_type: MutableActionType) -> None:
    """Add one action type, rejecting duplicate ids."""
    if action_type.type_id in _REGISTRY:
        raise ValueError(f"Mutable action type {action_type.type_id} already registered")
    _REGISTRY[action_type.type_id] = action_type


def get(type_id: str) -> MutableActionType | None:
    return _REGISTRY.get(type_id)


def all_types() -> tuple[MutableActionType, ...]:
    return tuple(_REGISTRY[name] for name in sorted(_REGISTRY))


def field_definitions(type_id: str, for_type: str) -> list[JsonObject]:
    """Field definitions of one subtype, or [] for unknown type/subtype."""
    action_type = get(type_id)
    if action_type is None:
        return []
    return action_type.field_definitions(for_type)


def _populate() -> None:
    # Import here so the registry module stays free of heavy model imports
    # until it is actually used.
    from .types_providers import ProviderUpdate
    from .types_servers import ServerGroupUpdate, ServerUpdate
    from .types_services import ServiceUpdate

    register(ProviderUpdate())
    register(ServiceUpdate())
    register(ServerGroupUpdate())
    register(ServerUpdate())


_populate()
