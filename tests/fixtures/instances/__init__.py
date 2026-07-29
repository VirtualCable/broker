"""
Service provider fixtures usable from tests.

The classes in :py:mod:`tests.fixtures.instances.service` are NOT auto-registered
by the production loader (``uds.services.__init__:initialize`` only walks
``src/uds/services/``). Tests opt in by calling
:py:func:`register_service_provider` on the classes they need, typically in
``setUp``/``setUpClass``.
"""

from uds.core.services import ServiceProvider


def register_service_provider(cls: type[ServiceProvider]) -> type[ServiceProvider]:
    """
    Register ``cls`` in the global service provider factory.

    Idempotent: registering a class whose ``type_type`` is already present is a
    no-op (the factory logs at debug and returns).
    """
    from uds.core.services import factory as _get_factory

    _get_factory().insert(cls)
    return cls
