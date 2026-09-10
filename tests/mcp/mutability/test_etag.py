"""Deterministic fingerprints (CAS base_etag support)."""

import unittest

from uds.mutability import item_etag


class ItemEtagTest(unittest.TestCase):
    """Deterministic fingerprints (CAS base_etag support)."""

    def test_is_stable(self) -> None:
        item = {"name": "pool", "comments": "x", "instance": {"cpu": 2}}
        self.assertEqual(item_etag(item, ["name", "comments"]), item_etag(item, ["name", "comments"]))

    def test_field_order_is_irrelevant(self) -> None:
        item = {"name": "pool", "comments": "x"}
        self.assertEqual(item_etag(item, ["name", "comments"]), item_etag(item, ["comments", "name"]))

    def test_changes_when_value_changes(self) -> None:
        fields = ["name", "cache_l1"]
        old = item_etag({"name": "p", "cache_l1": 2}, fields)
        new = item_etag({"name": "p", "cache_l1": 5}, fields)
        self.assertNotEqual(old, new)

    def test_ignores_unlisted_fields(self) -> None:
        fields = ["name"]
        base = item_etag({"name": "p", "other": 1}, fields)
        changed = item_etag({"name": "p", "other": 999}, fields)
        self.assertEqual(base, changed)

    def test_dot_paths(self) -> None:
        item = {"instance": {"cpu": {"low": 1}}, "name": "x"}
        self.assertEqual(item_etag(item, ["instance.cpu.low"]), item_etag(item, ["instance.cpu.low"]))

    def test_missing_field_behaves_like_empty(self) -> None:
        self.assertEqual(
            item_etag({"name": "p"}, ["name", "nonexistent"]),
            item_etag({"name": "p", "nonexistent": ""}, ["name", "nonexistent"]),
        )


if __name__ == "__main__":
    unittest.main()
