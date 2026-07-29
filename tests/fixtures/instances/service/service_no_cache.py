"""
Service without cache, no L1 / no L2 / no publication.
"""

from uds.core import services

from .user_service_no_cache import TestUserServiceNoCache


class TestServiceNoCache(services.Service):
    """Minimal non-cached service used by the test fixture."""

    type_name = "Test Service no cache"
    type_type = "TestServiceNoCache"
    type_description = "Test service without cache"
    icon_file = "service.png"

    uses_cache = False
    cache_tooltip = "None"
    uses_cache_l2 = False
    cache_tooltip_l2 = "None"

    needs_osmanager = False

    publication_type = None
    user_service_type = TestUserServiceNoCache
