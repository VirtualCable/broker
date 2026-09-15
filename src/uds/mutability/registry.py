"""Code-defined registry of mutable action types.

Concrete :class:`~uds.mutability.base.MutableActionType` classes living
in the :mod:`uds.mutability` package are discovered automatically
(modfinder style, same pattern as the REST dispatcher) and registered
once per process: an agent cannot invent action types or mutate anything
outside this surface — the mutability frontier is reviewed in code
review, not configurable at runtime.

Each class declares a root ``type_id`` plus the set of
:class:`~uds.mutability.base.ActionOperation` it implements; this module
expands the cross product into one full id per operation
(``provider.update``, ``servicepool.groups.set``, ``.add``, ``.get``,
...) and binds each to a zero-arg factory producing instances with that
operation set. Call sites keep the old contract: ``registry.get(id)()``
instantiates per use, so a type may hold per-request state.

Binding happens at registration: a declared operation must have a real
``op_*`` implementation (not the rejecting base default), so a class
that promises more than it delivers fails loudly at import, never on a
late execution.
"""

import collections.abc
import functools
import inspect
import typing

from uds.core.util import modfinder

from . import types as types
from .base import ActionOperation, JsonObject, MutableActionType

#: Operation dispatchable through ``MutableActionType.execute`` (GET is a
#: read binding served by ``read()``, never executed through a flow).
_DISPATCHABLE: typing.Final[frozenset[ActionOperation]] = frozenset(
    {ActionOperation.UPDATE, ActionOperation.SET, ActionOperation.ADD, ActionOperation.DELETE}
)


class Binding(typing.NamedTuple):
    """One full-id registration: the factory plus what it binds."""

    full_id: str
    operation: ActionOperation
    factory: collections.abc.Callable[[], MutableActionType]

    @property
    def proposable(self) -> bool:
        """True for write bindings (everything except the read-only GET)."""
        return self.operation is not ActionOperation.GET


_FACTORY = collections.abc.Callable[[], MutableActionType]

_REGISTRY: dict[str, Binding] = {}
_populated: bool = False


def register(action_type: type[MutableActionType] | MutableActionType) -> None:
    """Expand one action family into its full-id bindings, rejecting duplicates.

    Accepts the class (what modfinder passes) or an instance; only the
    class declarations (root ``type_id``, ``operations``, ``op_*``
    implementations) matter.
    """
    cls: type[MutableActionType] = action_type if isinstance(action_type, type) else type(action_type)
    if not cls.operations:
        raise ValueError(f"{cls.type_id}: declares no operations")
    for operation in cls.operations:
        _check_operation(cls, operation)
        full_id = f"{cls.type_id}.{operation.as_str()}"
        if full_id in _REGISTRY:
            raise ValueError(f"Mutable action type {full_id} already registered")
        _REGISTRY[full_id] = Binding(
            full_id=full_id,
            operation=operation,
            factory=typing.cast("_FACTORY", functools.partial(cls, operation)),
        )


def _check_operation(cls: type[MutableActionType], operation: ActionOperation) -> None:
    """A declared operation must resolve to a real implementation."""
    if operation not in _DISPATCHABLE | {ActionOperation.GET}:
        raise ValueError(f"{cls.__name__}: operation {operation.as_str()} cannot be registered")
    if operation is ActionOperation.GET:
        return  # served by the generic (overridable) MutableActionType.read
    hook = getattr(cls, f"op_{operation.as_str()}", None)
    default = getattr(MutableActionType, f"op_{operation.as_str()}")
    if hook is None or hook is default:
        raise ValueError(
            f"{cls.__name__}: declares the {operation.as_str()} operation but does not implement op_{operation.as_str()}"
        )


def get(type_id: str) -> _FACTORY | None:
    """Zero-arg factory bound to this full id, or None."""
    _populate()
    binding = _REGISTRY.get(type_id)
    return binding.factory if binding is not None else None


def binding(type_id: str) -> Binding | None:
    """Full registration entry of one id (id, operation, factory), or None."""
    _populate()
    return _REGISTRY.get(type_id)


def all_type_ids() -> tuple[str, ...]:
    """All registered full ids, sorted."""
    _populate()
    return tuple(sorted(_REGISTRY))


def all_bindings() -> tuple[Binding, ...]:
    """All registered bindings, sorted by full id."""
    _populate()
    return tuple(_REGISTRY[name] for name in sorted(_REGISTRY))


def field_definitions(type_id: str, for_type: str) -> list[JsonObject]:
    """Field definitions of one subtype, or [] for unknown type/subtype."""
    factory = get(type_id)
    if factory is None:
        return []
    return factory().field_definitions(for_type)


def _registrable(cls: type[MutableActionType]) -> bool:
    """Only concrete action types carrying their own ``type_id`` register."""
    return not inspect.isabstract(cls) and "type_id" in cls.__dict__


def load_package(module_name: str) -> None:
    """Discover and register the action types of one package (modfinder)."""
    modfinder.dynamically_load_and_register_packages(
        register,
        MutableActionType,
        module_name=module_name,
        checker=_registrable,
    )


def _populate() -> None:
    """Auto-load the concrete action types of ``types/`` (once).

    The flag is process-wide on purpose: a test (or anyone) clearing the
    registry must not get it silently re-populated on the next lookup.
    """
    global _populated
    if _populated:
        return
    _populated = True
    load_package(types.__name__)


_populate()
