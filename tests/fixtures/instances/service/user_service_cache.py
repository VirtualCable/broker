"""
UserService paired with :py:class:`TestServiceWithCache`.

``TestServiceWithCache`` is imported in :py:func:`typing.TYPE_CHECKING` only:
``service()`` override uses a string forward reference and ``typing.cast`` for
the return type, so we do not introduce a runtime cycle between the two
sibling modules.
"""

import typing

from uds.core import services
from uds.core.util import autoserializable

if typing.TYPE_CHECKING:
    from .service_cache import TestServiceWithCache


class TestUserServiceWithCache(services.UserService, autoserializable.AutoSerializable):
    """UserService used by :py:class:`TestServiceWithCache` (L1+L2)."""

    @typing.override
    def service(self) -> "TestServiceWithCache":
        return typing.cast("TestServiceWithCache", super().service())
