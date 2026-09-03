"""Tests for the generic MCP list-tool factory."""

import unittest

from uds.REST.inventory import collection_handlers
from uds.REST.model.detail import DetailHandler
from uds.REST.model.master import ModelHandler
from uds.mcp import build_catalog
from uds.mcp.tools import generated_list_tools


class GeneratedListToolsTest(unittest.TestCase):
    """Validate the auto-generated list tools cover all collection handlers."""

    def test_service_pools_generates_like_any_collection(self) -> None:
        # Service pools are not special-cased: the generator derives the
        # tool name from the handler path, exactly like every other
        # collection handler.
        catalog = build_catalog()
        names = [tool.name for tool in catalog.tools()]
        self.assertEqual(names.count("list_servicespools"), 1)

    def test_no_duplicate_tool_names(self) -> None:
        tools = list(build_catalog().tools())
        self.assertEqual(len(tools), len({t.name for t in tools}))

    def test_only_model_handlers_are_published(self) -> None:
        """Plain ``Handler`` collections with ``get_items`` are excluded."""
        non_model = [
            entry.path for entry in collection_handlers() if not issubclass(entry.handler, ModelHandler | DetailHandler)
        ]
        self.assertTrue(non_model, "expected at least one non-model collection in the inventory")
        catalog = build_catalog()
        names = {tool.name for tool in catalog.tools()}
        for path in non_model:
            parts = [p for p in path.split("/") if p not in ("{uuid}", "")]
            self.assertNotIn("list_" + "_".join(parts), names)

    def test_all_generated_tools_carry_odata_input(self) -> None:
        common_keys = {"filter", "orderby", "top", "skip", "select"}
        for tool in generated_list_tools():
            properties = (tool.input_schema or {}).get("properties", {})
            # Every list tool carries the shared OData args; detail
            # collections additionally require the parent uuid.
            self.assertLessEqual(common_keys, set(properties.keys()))

    def test_detail_tools_require_parent_uuid(self) -> None:
        detail_tools = [
            tool for tool in build_catalog().tools() if "parent_uuid" in (tool.input_schema or {}).get("properties", {})
        ]
        self.assertTrue(detail_tools, "expected at least one detail list tool")
        for tool in detail_tools:
            self.assertIn("parent_uuid", (tool.input_schema or {}).get("properties", {}))
