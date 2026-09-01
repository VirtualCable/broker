"""Tests for the MCP OData-to-REST argument translation."""

import unittest

from uds.mcp.rest_proxy import ODataArgs, odata_params_from


class ODataTranslationTest(unittest.TestCase):
    """Validate the structured OData argument translation."""

    def test_structured_fields_are_translated(self) -> None:
        params = odata_params_from(
            {
                "filter": "state eq ACTIVE",
                "orderby": "name",
                "top": 5,
                "skip": 10,
                "select": ["id", "name", "state"],
            }
        )
        self.assertEqual(params["$filter"], "state eq ACTIVE")
        self.assertEqual(params["$orderby"], "name")
        self.assertEqual(params["$top"], 5)
        self.assertEqual(params["$skip"], 10)
        self.assertEqual(params["$select"], "id,name,state")

    def test_typed_args_are_translated(self) -> None:
        params = odata_params_from(ODataArgs(filter="x", top=10))
        self.assertEqual(params, {"$filter": "x", "$top": 10})

    def test_raw_dollar_keys_are_kept(self) -> None:
        params = odata_params_from({"$filter": "name eq foo", "$top": 3})
        self.assertEqual(params, {"$filter": "name eq foo", "$top": 3})

    def test_unsupported_inputs_return_empty(self) -> None:
        self.assertEqual(odata_params_from(None), {})
        self.assertEqual(odata_params_from("not a dict"), {})
        self.assertEqual(odata_params_from(42), {})
