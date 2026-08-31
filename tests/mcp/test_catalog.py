"""Tests for the curated MCP catalog and response redaction."""

import unittest

from uds.mcp import Catalog, REDACTED, ResourceDefinition, ToolDefinition, redact


class CatalogTest(unittest.TestCase):
    """Verify catalog invariants used by MCP registration and skill generation."""

    def test_tools_and_resources_are_stable(self) -> None:
        """Catalog entries are returned in deterministic order."""
        catalog = Catalog()
        catalog.add_tool(ToolDefinition("z-tool", "Z", "Z tool", {}, "users", "Z result"))
        catalog.add_tool(ToolDefinition("a-tool", "A", "A tool", {}, "users", "A result"))
        catalog.add_resource(ResourceDefinition("uds://z", "z", "Z", "Z resource", "users", "Z result"))
        catalog.add_resource(ResourceDefinition("uds://a", "a", "A", "A resource", "users", "A result"))

        self.assertEqual([tool.name for tool in catalog.tools()], ["a-tool", "z-tool"])
        self.assertEqual([resource.uri for resource in catalog.resources()], ["uds://a", "uds://z"])

    def test_duplicate_entries_are_rejected(self) -> None:
        """Duplicate names and URIs cannot silently replace catalog entries."""
        catalog = Catalog()
        tool = ToolDefinition("same", "Same", "Same tool", {}, "users", "result")
        resource = ResourceDefinition("uds://same", "same", "Same", "Same resource", "users", "result")

        catalog.add_tool(tool)
        catalog.add_resource(resource)
        with self.assertRaises(ValueError):
            catalog.add_tool(tool)
        with self.assertRaises(ValueError):
            catalog.add_resource(resource)


class RedactionTest(unittest.TestCase):
    """Verify recursive redaction of known sensitive field names."""

    def test_nested_sensitive_fields_are_redacted(self) -> None:
        """Sensitive fields are removed from mappings at every nesting level."""
        value = {
            "name": "server",
            "token": "secret-token",
            "nested": {"PASSWORD": "secret-password"},
            "items": [{"private_key": "secret-key", "value": 1}],
        }

        self.assertEqual(
            redact(value),
            {
                "name": "server",
                "token": REDACTED,
                "nested": {"PASSWORD": REDACTED},
                "items": [{"private_key": REDACTED, "value": 1}],
            },
        )
