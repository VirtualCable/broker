"""Code-defined registry of mutable action types.

Concrete :class:`~uds.mutability.base.MutableActionType` classes living
in the :mod:`uds.mutability` package are discovered automatically
(modfinder style, same pattern as the REST dispatcher) and registered
once per process: an agent cannot invent action types or mutate anything
outside this surface — the mutability frontier is reviewed in code
review, not configurable at runtime.

The registry stores classes; call sites instantiate per use, so a type
may keep per-request state without refactoring.
"""

import inspect

from uds.core.util import modfinder

from .base import JsonObject, MutableActionType

_REGISTRY: dict[str, type[MutableActionType]] = {}
_populated: bool = False


def register(action_type: type[MutableActionType] | MutableActionType) -> None:
    """Add one action type (class or instance), rejecting duplicate ids."""
    cls: type[MutableActionType] = action_type if isinstance(action_type, type) else type(action_type)
    if cls.type_id in _REGISTRY:
        raise ValueError(f"Mutable action type {cls.type_id} already registered")
    _REGISTRY[cls.type_id] = cls


def get(type_id: str) -> type[MutableActionType] | None:
    """Class registered under this id (instantiate it before use), or None."""
    _populate()
    return _REGISTRY.get(type_id)


def all_types() -> tuple[type[MutableActionType], ...]:
    """All registered action type classes, sorted by type id."""
    _populate()
    return tuple(_REGISTRY[name] for name in sorted(_REGISTRY))


def field_definitions(type_id: str, for_type: str) -> list[JsonObject]:
    """Field definitions of one subtype, or [] for unknown type/subtype."""
    action_type = get(type_id)
    if action_type is None:
        return []
    return action_type().field_definitions(for_type)


def _registrable(cls: type[MutableActionType]) -> bool:
    """Only concrete action types carrying their own ``type_id`` register."""
    return not inspect.isabstract(cls) and "type_id" in cls.__dict__


def _populate() -> None:
    """Discover and register the action types of the package (once).

    The flag is process-wide on purpose: a test (or anyone) clearing the
    registry must not get it silently re-populated on the next lookup.
    """
    global _populated
    if _populated:
        return
    _populated = True
    modfinder.dynamically_load_and_register_packages(
        register,
        MutableActionType,
        module_name=__name__[: __name__.rfind(".")],
        checker=_registrable,
    )


_populate()
