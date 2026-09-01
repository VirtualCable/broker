"""Tests for the REST inventory walker."""

import unittest
import typing

from uds.REST.inventory import (
    HandlerInventoryEntry,
    collection_handlers,
    invalidate,
    walk_rest_handlers,
)
from uds.REST.methods.services_pools import ServicesPools


class RestVisitorTest(unittest.TestCase):
    """Validate the cached inventory walker."""

    @typing.override
    def setUp(self) -> None:
        invalidate()

    def test_walk_returns_collection_handlers(self) -> None:
        names = {entry.name for entry in walk_rest_handlers()}
        self.assertIn("servicespools", names)
        self.assertIn("providers", names)
        self.assertIn("transports", names)

    def test_collection_handlers_expose_get_items(self) -> None:
        names = {entry.name for entry in collection_handlers()}
        self.assertIn("servicespools", names)

    def test_entries_are_deterministic(self) -> None:
        first = walk_rest_handlers()
        second = walk_rest_handlers()
        self.assertEqual([e.name for e in first], [e.name for e in second])

    def test_custom_methods_are_collected(self) -> None:
        entry = next(e for e in walk_rest_handlers() if e.name == "servicespools")
        custom = {cm.name for cm in entry.custom_methods}
        self.assertIn("fallback_access", custom)

    def test_invalidate_forces_recomputation(self) -> None:
        walk_rest_handlers()
        invalidate()
        # After invalidation the cache should rebuild; the call must
        # still succeed.
        walk_rest_handlers()


class InventoryEntryTest(unittest.TestCase):
    """Cover small helpers on HandlerInventoryEntry."""

    def test_supported_http_methods(self) -> None:
        entry = HandlerInventoryEntry(
            handler=ServicesPools,
            name="servicespools",
            path="servicespools",
            role=None,
            is_collection=True,
            exposes_get_items=True,
            custom_methods=(),
            api_operations=("get", "post"),
            description="",
        )
        self.assertIn("GET", entry.supported_http_methods())
        self.assertIn("POST", entry.supported_http_methods())
        self.assertIn("QUERY", entry.supported_http_methods())
        self.assertNotIn("DELETE", entry.supported_http_methods())
