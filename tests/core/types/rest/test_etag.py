"""Tests for the REST item ETag (deterministic field ordering)."""

import dataclasses
import hashlib
import typing
import unittest

from uds.core.types.rest import BaseRestItem


@dataclasses.dataclass
class _SampleItem(BaseRestItem):
    name: str = "item"
    comments: str = ""
    a: int = 1
    b: int = 2
    c: int = 3
    d: int = 4
    e: int = 5
    f: int = 6
    nested: dict[str, int] = dataclasses.field(default_factory=dict[str, int])


class ETagTest(unittest.TestCase):
    """ETag values must be repeatable across processes."""

    @typing.override
    def setUp(self) -> None:
        self.item = _SampleItem(nested={"cpu": 4, "memory": 1024})

    def test_inmutables_uses_sorted_field_order(self) -> None:
        # Fields are joined in alphabetical order. A plain ``set`` iteration
        # is randomized per process, which would make the same item produce
        # different hashes on different workers (breaking If-Match).
        fields = ("f", "d", "b", "name", "a", "c", "e", "comments")
        expected = "".join(
            [
                str(self.item.a),
                str(self.item.b),
                str(self.item.c),
                str(self.item.comments or ""),
                str(self.item.d),
                str(self.item.e),
                str(self.item.f),
                str(self.item.name),
            ]
        )
        self.assertEqual(self.item.inmutables(*fields), expected)

    def test_etag_is_sha256_of_inmutables(self) -> None:
        fields = ("name", "comments", "a", "b")
        self.assertEqual(
            self.item.etag(*fields),
            hashlib.sha256(self.item.inmutables(*fields).encode("utf-8")).hexdigest(),
        )

    def test_etag_is_order_independent(self) -> None:
        self.assertEqual(
            self.item.etag("name", "a", "b", "c", "d"),
            self.item.etag("d", "c", "b", "a", "name"),
        )

    def test_dot_paths_are_extracted(self) -> None:
        self.assertEqual(self.item.inmutables("nested.cpu"), "4")
        self.assertEqual(self.item.inmutables("nested.missing"), str(self.item.nested.get("missing") or ""))

    def test_missing_field_behaves_like_empty(self) -> None:
        self.assertEqual(self.item.inmutables("nonexistent"), "")


if __name__ == "__main__":
    unittest.main()
