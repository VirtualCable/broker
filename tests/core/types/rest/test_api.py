"""
Unit tests for the OData parameters parsing (``ODataParams``).
"""

import unittest

from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.rest.api import ODataParams


class ODataParamsTest(unittest.TestCase):
    def _orderby(self, value: str) -> list[str]:
        return ODataParams.from_dict({"$orderby": value}).orderby

    def test_valid_orderby_asc_desc(self) -> None:
        self.assertEqual(self._orderby("name asc"), ["name"])
        self.assertEqual(self._orderby("name desc"), ["-name"])

    def test_valid_orderby_multiple_fields(self) -> None:
        self.assertEqual(self._orderby("name asc, created desc"), ["name", "-created"])

    def test_valid_orderby_related_field_dot_notation(self) -> None:
        self.assertEqual(
            self._orderby("deployed_service.publications.revision desc"), ["-deployed_service__publications__revision"]
        )

    def test_invalid_orderby_underscore_prefix_rejected(self) -> None:
        with self.assertRaises(rest_exceptions.RequestError):
            ODataParams.from_dict({"$orderby": "_connector"})

    def test_invalid_orderby_double_underscore_rejected(self) -> None:
        with self.assertRaises(rest_exceptions.RequestError):
            ODataParams.from_dict({"$orderby": "user__password desc"})

    def test_invalid_orderby_empty_rejected(self) -> None:
        with self.assertRaises(rest_exceptions.RequestError):
            ODataParams.from_dict({"$orderby": "   "})

    def test_invalid_odata_values_raise(self) -> None:
        with self.assertRaises(rest_exceptions.RequestError):
            ODataParams.from_dict({"$top": "abc"})
