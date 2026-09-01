"""Tests for the generic MCP list-tool factory."""

import unittest

from uds.mcp import build_catalog
from uds.mcp.tools import (
    generated_list_tools,
    pluralize,
)


class PluralizeTest(unittest.TestCase):
    """Validate the english pluralization used by the tool factory."""

    def test_simple_singulars(self) -> None:
        self.assertEqual(pluralize("provider"), "providers")
        self.assertEqual(pluralize("user"), "users")
        self.assertEqual(pluralize("transport"), "transports")

    def test_y_to_ies(self) -> None:
        self.assertEqual(pluralize("category"), "categories")

    def test_singular_passthrough(self) -> None:
        self.assertEqual(pluralize("users"), "users")
        self.assertEqual(pluralize("providers"), "providers")


class GeneratedListToolsTest(unittest.TestCase):
    """Validate the auto-generated list tools cover all collection handlers."""

    def test_curated_name_wins_over_generic(self) -> None:
        # The generator excludes ``list_service_pools`` so the curated
        # entry from ``build_catalog`` survives and is unique.
        generated_names = {tool.name for tool in generated_list_tools()}
        self.assertNotIn("list_service_pools", generated_names)

        catalog = build_catalog()
        names = [tool.name for tool in catalog.tools()]
        self.assertEqual(names.count("list_service_pools"), 1)
        service_pools = next(t for t in catalog.tools() if t.name == "list_service_pools")
        self.assertEqual(service_pools.title, "List service pools")

    def test_no_duplicate_tool_names(self) -> None:
        tools = list(build_catalog().tools())
        self.assertEqual(len(tools), len({t.name for t in tools}))

    def test_all_generated_tools_carry_odata_input(self) -> None:
        schema_keys = {"filter", "orderby", "top", "skip", "select"}
        for tool in generated_list_tools():
            properties = (tool.input_schema or {}).get("properties", {})
            self.assertEqual(set(properties.keys()), schema_keys)
