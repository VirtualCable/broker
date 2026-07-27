"""
Minimal Provider for the in-tree service-provider test fixture.

Lives under ``tests/fixtures/instances/service/`` and is NOT picked up by
``src/uds/services/__init__.py:initialize()``. Tests opt in via
:py:func:`tests.fixtures.instances.register_service_provider`.
"""

from uds.core import services

from .service_cache import TestServiceWithCache
from .service_no_cache import TestServiceNoCache


class TestInstancesProvider(services.ServiceProvider):
    """Minimal Provider exposing one with-cache and one no-cache service."""

    offers = [TestServiceWithCache, TestServiceNoCache]

    type_name = "Test Instances Provider"
    type_type = "TestInstancesProvider"
    type_description = "Test (and dummy) service provider for the in-tree test fixture"
    icon_file = "provider.png"

    concurrent_creation_limit = 1000
    concurrent_removal_limit = 1000
