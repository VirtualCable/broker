"""
UserService paired with :py:class:`TestServiceNoCache`.

``TestServiceNoCache`` is imported in :py:func:`typing.TYPE_CHECKING` only;
``service()`` override uses a string forward reference and ``typing.cast``.
"""

import typing

from uds.core import services
from uds.core.util import autoserializable

if typing.TYPE_CHECKING:
    from .service_no_cache import TestServiceNoCache


class TestUserServiceNoCache(services.UserService, autoserializable.AutoSerializable):
    """UserService used by :py:class:`TestServiceNoCache` (no cache)."""

    @typing.override
    def service(self) -> "TestServiceNoCache":
        return typing.cast("TestServiceNoCache", super().service())
