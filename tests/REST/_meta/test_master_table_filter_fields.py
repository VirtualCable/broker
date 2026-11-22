"""Every registered master handler declares filterable fields.

Since the OData layer restricts ``$filter``/``$orderby`` to the fields the
handler's table declares (see ``uds.REST.handlers``), a master handler
whose ``TABLE`` has no ``filter_fields`` silently keeps the unrestricted
default. A forgotten declaration is a security gap, not a cosmetic
detail, so it must fail a test instead.

The dispatcher only registers leaf handlers (classes with subclasses are
skipped, see ``Dispatcher.initialize``), so abstract surfaces
(``FlowsAdminSurface`` and friends) are excluded with the same rule.

Mirrors ``tests/REST/_meta/test_detail_table_filter_fields.py``.
"""

import collections.abc
import importlib
import typing

from uds.REST.model.master import ModelHandler

# Ensure every methods module is imported so the subclass tree is complete
importlib.import_module("uds.REST.dispatcher")

_MasterHandler: typing.TypeAlias = type[ModelHandler[typing.Any]]


def _descendants(cls: _MasterHandler) -> collections.abc.Iterator[_MasterHandler]:
    for subclass in cls.__subclasses__():
        yield subclass
        yield from _descendants(subclass)


def _registered_masters() -> list[_MasterHandler]:
    """Leaf ModelHandlers, exactly what ``Dispatcher.initialize`` registers."""
    return [
        handler
        for handler in _descendants(ModelHandler)
        if not handler.__subclasses__() and getattr(handler, "MODEL", None) is not None
    ]


def test_every_registered_master_declares_filter_fields() -> None:
    registered = _registered_masters()
    assert registered, "no registered master handler found, registration is broken"
    missing = sorted(handler.__qualname__ for handler in registered if not handler.TABLE.filter_fields)
    assert not missing, f"registered masters without filter_fields (OData unrestricted): {missing}"
