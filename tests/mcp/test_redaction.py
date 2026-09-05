"""Tests for the defensive redaction of MCP responses."""

import typing
import unittest

from uds.mcp.redaction import REDACTED, SENSITIVE_FIELDS, redact


class RedactionTest(unittest.TestCase):
    """Redaction is key-name based (denylist), recursive and total on names."""

    def test_every_sensitive_name_is_redacted(self) -> None:
        """Each name on the list must be replaced, whatever the value."""
        for name in SENSITIVE_FIELDS:
            value = {name: "top-secret-value"}
            self.assertEqual(redact(value), {name: REDACTED})

    def test_sensitive_names_are_matched_case_insensitively(self) -> None:
        """Keys are matched case-insensitively (``Token``, ``PASSWORD``...)."""
        value = {"Token": "a", "PASSWORD": "b", "SeCrEt_KeY": "c"}
        self.assertEqual(redact(value), {"Token": REDACTED, "PASSWORD": REDACTED, "SeCrEt_KeY": REDACTED})

    def test_recursion_covers_nested_containers(self) -> None:
        """Nested mappings, lists and tuples are walked and redacted."""
        value: dict[str, typing.Any] = {
            "ok": {
                "nested": [
                    {"token": "x", "name": "kept"},
                    ("password", {"pair": "y"}),
                ]
            }
        }
        self.assertEqual(
            redact(value),
            {"ok": {"nested": [{"token": REDACTED, "name": "kept"}, ("password", {"pair": "y"})]}},
        )

    def test_innocuous_names_survive(self) -> None:
        """Ordinary fields are not touched, so data is not distorted."""
        value = {"name": "kept", "uuid": "abc", "id": 7, "count": 3}
        self.assertEqual(redact(value), value)

    def test_scalars_pass_through(self) -> None:
        """Scalar values (non-mapping, non-sequence) are returned untouched."""
        for scalar in ("str", 7, 3.5, True, None):
            self.assertEqual(redact(scalar), scalar)

    def test_extra_keys_are_unioned_with_global_denylist(self) -> None:
        """Per-call ``extra_keys`` add to (not replace) the global denylist.

        When no curated tool or resource declares fields, the function must
        behave exactly as before: only the global ``SENSITIVE_FIELDS`` list
        applies. When one does declare fields, those fields are redacted on
        top of the global list.
        """
        value = {"public": "ok", "service_inventory_token": "leaked", "token": "leaked"}
        # Extra key catches a field the global denylist would miss.
        self.assertEqual(
            redact(value, ("service_inventory_token",)),
            {"public": "ok", "service_inventory_token": REDACTED, "token": REDACTED},
        )
        # Empty / default ``extra_keys`` equals the historical behaviour.
        self.assertEqual(redact({"token": "x"}), {"token": REDACTED})
        self.assertEqual(redact({"token": "x"}, ()), {"token": REDACTED})
        # A non-string entry in ``extra_keys`` is ignored without crashing.
        self.assertEqual(
            redact({"service_inventory_token": "y"}, ("service_inventory_token", 42)),
            {"service_inventory_token": REDACTED},
        )
