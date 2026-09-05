"""Tests for the REST inventory walker."""

import unittest

from uds.REST.inventory import (
    HandlerInventoryEntry,
    collection_handlers,
    walk_rest_handlers,
)
from uds.REST.methods.services_pools import ServicesPools


class RestWalkerTest(unittest.TestCase):
    """Validate the walker over the real dispatcher tree."""

    def test_walk_returns_master_handlers(self) -> None:
        names = {entry.name for entry in walk_rest_handlers()}
        self.assertIn("servicespools", names)
        self.assertIn("providers", names)
        self.assertIn("transports", names)

    def test_walk_returns_system_and_version(self) -> None:
        names = {entry.name for entry in walk_rest_handlers()}
        self.assertIn("system", names)
        self.assertIn("version", names)

    def test_walk_emits_detail_entries_with_parent(self) -> None:
        entries = [e for e in walk_rest_handlers() if e.is_detail and e.name == "users"]
        self.assertTrue(entries, "expected at least one detail entry named 'users'")
        for entry in entries:
            self.assertIn("{uuid}", entry.path)
            assert entry.parent is not None
            self.assertEqual(entry.parent.name, "authenticators")

    def test_accept_filter_drops_entries(self) -> None:
        accepted = walk_rest_handlers(lambda e: e.name == "providers")
        self.assertEqual([e.name for e in accepted], ["providers"])

    def test_collection_handlers_include_master_and_detail(self) -> None:
        names = {entry.name for entry in collection_handlers()}
        self.assertIn("servicespools", names)
        self.assertIn("users", names)

    def test_entries_are_deterministic(self) -> None:
        first = walk_rest_handlers()
        second = walk_rest_handlers()
        self.assertEqual([(e.name, e.path) for e in first], [(e.name, e.path) for e in second])

    def test_custom_methods_are_collected(self) -> None:
        entry = next(e for e in walk_rest_handlers() if e.name == "servicespools")
        custom = {cm.name for cm in entry.custom_methods}
        self.assertIn("fallback_access", custom)


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

    def test_full_path_and_is_detail(self) -> None:
        master = HandlerInventoryEntry(
            handler=ServicesPools,
            name="servicespools",
            path="servicespools",
            role=None,
            is_collection=True,
            exposes_get_items=True,
            custom_methods=(),
            api_operations=(),
            description="",
        )
        detail = HandlerInventoryEntry(
            handler=ServicesPools,
            name="users",
            path="authenticators/{uuid}/users",
            role=None,
            is_collection=True,
            exposes_get_items=True,
            custom_methods=(),
            api_operations=(),
            description="",
            parent=master,
        )
        self.assertEqual(master.full_path, "/servicespools")
        self.assertFalse(master.is_detail)
        self.assertEqual(detail.full_path, "/authenticators/{uuid}/users")
        self.assertTrue(detail.is_detail)
