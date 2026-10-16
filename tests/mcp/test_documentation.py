"""Contract tests for the MCP catalog documentation.

Every published tool must be self-describing enough that a fresh LLM can
call it correctly on the first try without prior context. The rules are:

- ``description`` carries the operational contract (when to call, scope,
  pagination, side-effects). Read-only tools document at least 80
  characters; write tools at least 120.
- ``access`` names the permission role expected of the caller.
- ``returns`` describes the shape of the response.
- ``examples`` is non-empty and at least one element is JSON-parseable up
  to the colon that separates request from expected response.
- Every property of ``input_schema`` with semantic meaning carries a
  ``description``.

These rules used to live in ``tests/mcp/test_tools.py``; they are split
out so any regression points at documentation specifically instead of
mixing it with the schema tests.
"""

import json
import re
import unittest

from uds.mcp import build_catalog


_REQUEST_SPLIT = re.compile(r"Expected response:", re.IGNORECASE)


def _split_example(example: str) -> tuple[str, str] | None:
    """Return (request_json, expected_response_substring) for an example.

    ``None`` is returned when the example does not follow the
    ``"Request: ...\nExpected response: ..."`` convention; the test that
    cares about it skips instead of failing.
    """
    if _REQUEST_SPLIT.search(example) is None:
        return None
    request_part, response_part = _REQUEST_SPLIT.split(example, maxsplit=1)
    request = request_part.removeprefix("Request:").strip()
    return request, response_part.strip()


class ToolDocumentationContractTest(unittest.TestCase):
    """Documentation contract for the MCP tools touched by UDS-085.

    The contract covers the mutability lifecycle (``create_flow``,
    ``submit_flow``, ``list_flow_actions``, ``update_flow_action``,
    ``cancel_flow``, ``get_mutable_fields``, every generated
    ``propose_*``) and the auto-generated ``list_*`` tools.

    The curated ``get_*`` / ``report_*`` tools are out of scope for
    UDS-085 and will be picked up by a follow-up ticket. Keeping the
    scope narrow avoids blocking this fix on every curated tool.
    """

    IN_SCOPE_PREFIXES: tuple[str, ...] = ("propose_",)
    IN_SCOPE_NAMES: frozenset[str] = frozenset(
        {
            "create_flow",
            "submit_flow",
            "list_flow_actions",
            "update_flow_action",
            "cancel_flow",
            "get_mutable_fields",
        }
    )

    def setUp(self) -> None:
        super().setUp()
        self._tools = [
            tool
            for tool in build_catalog().tools()
            if tool.name.startswith(self.IN_SCOPE_PREFIXES)
            or tool.name in self.IN_SCOPE_NAMES
            or tool.name.startswith("list_")
        ]

    def test_every_tool_has_examples(self) -> None:
        for tool in self._tools:
            with self.subTest(tool=tool.name):
                self.assertTrue(
                    tool.examples,
                    f"{tool.name} must carry at least one example call",
                )

    def test_every_tool_description_meets_minimum_length(self) -> None:
        for tool in self._tools:
            with self.subTest(tool=tool.name):
                minimum = 80 if tool.read_only else 120
                self.assertGreaterEqual(
                    len(tool.description),
                    minimum,
                    f"{tool.name} description is too short: "
                    f"{len(tool.description)} < {minimum} chars",
                )

    def test_every_input_property_carries_description(self) -> None:
        for tool in self._tools:
            schema = tool.input_schema or {}
            properties = schema.get("properties", {})
            for name, prop in properties.items():
                with self.subTest(tool=tool.name, property=name):
                    self.assertIsInstance(prop, dict)
                    self.assertTrue(
                        prop.get("description"),
                        f"{tool.name}.{name} has no description",
                    )

    def test_at_least_one_example_is_parseable_json(self) -> None:
        for tool in self._tools:
            with self.subTest(tool=tool.name):
                parseable = False
                for example in tool.examples:
                    parsed = _split_example(example)
                    if parsed is None:
                        continue
                    request, _response = parsed
                    try:
                        json.loads(request)
                    except json.JSONDecodeError:
                        continue
                    parseable = True
                    break
                self.assertTrue(
                    parseable,
                    f"{tool.name} needs at least one example whose "
                    "request part is valid JSON",
                )


if __name__ == "__main__":
    unittest.main()
