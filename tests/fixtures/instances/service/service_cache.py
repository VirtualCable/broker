"""
Service with L1+L2 cache. Requires ``TestPublication`` so the factory accepts it.
"""

from uds.core import services

from .publication import TestPublication
from .user_service_cache import TestUserServiceWithCache


class TestServiceWithCache(services.Service):
    """Minimal cached service used by the test fixture."""

    type_name = "Test Service with Cache"
    type_type = "TestServiceWithCache"
    type_description = "Test service with L1+L2 cache"
    icon_file = "service.png"

    uses_cache = True
    cache_tooltip = "L1 cache (test)"
    uses_cache_l2 = True
    cache_tooltip_l2 = "L2 cache (test)"

    needs_osmanager = False

    publication_type = TestPublication
    user_service_type = TestUserServiceWithCache
