"""Tests for the MCP tool-argument validator."""

import typing
import unittest

from uds.mcp import validate_arguments

_LIST_SCHEMA: typing.Final[dict[str, typing.Any]] = {
    "type": "object",
    "properties": {
        "filter": {"type": "string"},
        "top": {"type": "integer", "minimum": 1},
        "skip": {"type": "integer", "minimum": 0},
        "select": {"type": "array", "items": {"type": "string"}},
        "parent_uuid": {"type": "string"},
    },
    "additionalProperties": False,
}


class ValidateArgumentsTest(unittest.TestCase):
    """Validate the closed JSON-Schema subset the catalog publishes."""

    def test_valid_arguments_pass(self) -> None:
        validate_arguments(_LIST_SCHEMA, {"filter": "name eq x", "top": 5, "select": ["id", "name"]})

    def test_non_object_arguments_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_arguments(_LIST_SCHEMA, ["not", "an", "object"])

    def test_unknown_argument_is_rejected_with_allowed_list(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_arguments(_LIST_SCHEMA, {"bogus": 1})
        self.assertIn("bogus", str(ctx.exception))
        self.assertIn("filter", str(ctx.exception))

    def test_wrong_type_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_arguments(_LIST_SCHEMA, {"top": "abc"})
        self.assertIn("top", str(ctx.exception))
        self.assertIn("integer", str(ctx.exception))

    def test_bool_is_not_an_integer(self) -> None:
        with self.assertRaises(ValueError):
            validate_arguments(_LIST_SCHEMA, {"top": True})

    def test_minimum_is_enforced(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_arguments(_LIST_SCHEMA, {"top": 0})
        self.assertIn(">= 1", str(ctx.exception))

    def test_array_item_type_is_enforced(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_arguments(_LIST_SCHEMA, {"select": ["ok", 42]})
        self.assertIn("select", str(ctx.exception))

    def test_empty_arguments_pass(self) -> None:
        validate_arguments(_LIST_SCHEMA, {})
