"""Tests for the reports endpoint verb contract.

Report generation creates a new document, so ``POST /reports/{uuid}`` is
the canonical verb; the legacy ``PUT`` stays in COMPAT mode and answers
with the standard deprecation headers. The report generation itself is
mocked out here: what these tests pin is the verb dispatch, the shared
answer shape and the deprecation headers, not each report's content.
"""

import json
import typing
from unittest import mock

from uds.REST.methods.reports import Reports

from tests.utils import rest
from tests.utils.test import UDSHttpResponse


class _FakeReport:
    """Minimal stand-in for a generated report."""

    mime_type = "text/csv"
    encoded = False
    filename = "fake-report.csv"

    def generate_encoded(self) -> str:
        return "col1,col2\n1,2\n"


class ReportsVerbTest(rest.test.RESTTestCase):
    """POST is canonical, PUT is a deprecated alias with the same answer."""

    REPORT_UUID: typing.Final[str] = "some-report-uuid"

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        # Bearer (API token) authentication: the legacy ``X-Auth-Token``
        # session login would itself flag every response as deprecated.
        self.login_with_api_token()

    def _post(self, path: str, data: dict[str, typing.Any] | None = None) -> UDSHttpResponse:
        with mock.patch.object(Reports, "_locate_report", return_value=_FakeReport()):
            return self.client.rest_post(path, data=data or {})

    def _put(self, path: str, data: dict[str, typing.Any] | None = None) -> UDSHttpResponse:
        with mock.patch.object(Reports, "_locate_report", return_value=_FakeReport()):
            return self.client.rest_put(path, data=data or {})

    def test_post_generates_the_report(self) -> None:
        response = self._post(f"reports/{self.REPORT_UUID}")
        self.assertEqual(response.status_code, 200, response.content)
        body = typing.cast("dict[str, typing.Any]", json.loads(response.content))
        self.assertEqual(body["mime_type"], "text/csv")
        self.assertEqual(body["filename"], "fake-report.csv")
        self.assertEqual(body["data"], "col1,col2\n1,2\n")
        # The canonical verb must not advertise deprecation.
        self.assertNotIn("X-UDS-Deprecated", response.headers)

    def test_put_generates_with_deprecation_headers(self) -> None:
        response = self._put(f"reports/{self.REPORT_UUID}")
        self.assertEqual(response.status_code, 200, response.content)
        body = typing.cast("dict[str, typing.Any]", json.loads(response.content))
        self.assertEqual(body["data"], "col1,col2\n1,2\n")
        self.assertIn("Deprecation", response.headers)
        self.assertEqual(response.headers["X-UDS-Deprecated"], "true")
        self.assertIn(f"use POST /reports/{self.REPORT_UUID}", response.headers["X-UDS-Deprecated-Reason"])

    def test_post_without_uuid_is_rejected(self) -> None:
        response = self._post("reports")
        self.assertEqual(response.status_code, 400, response.content)

    def test_put_without_uuid_is_rejected(self) -> None:
        response = self._put("reports")
        self.assertEqual(response.status_code, 400, response.content)
        # Rejected before any generation: no deprecation advertisement.
        self.assertNotIn("X-UDS-Deprecated", response.headers)

    def test_reports_are_listed(self) -> None:
        response = self.client.rest_get("reports")
        self.assertEqual(response.status_code, 200, response.content)
        listing = typing.cast("list[dict[str, typing.Any]]", json.loads(response.content))
        self.assertIsInstance(listing, list)
        self.assertTrue(listing)  # the factory always registers reports
