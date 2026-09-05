#
# Copyright (c) 2026 Virtual Cable S.L.U.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright notice,
#      this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright notice,
#      this list of conditions and the following disclaimer in the documentation
#      and/or other materials provided with the distribution.
#    * Neither the name of Virtual Cable S.L.U. nor the names of its contributors
#      may be used to endorse or promote products derived from this software
#      without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
Unit tests for the report PDF pipeline and its custom URL fetcher.

Covers ``_ReportFetcher`` (``stock://``, ``image://`` and delegation to
WeasyPrint's default fetcher) plus end-to-end ``Report.as_pdf()``
rendering, so refactors of the WeasyPrint integration don't silently
break report generation.
"""

import logging

import pytest

from uds.core.reports import stock
from uds.core.reports.report import Report
from uds.core.reports.report import _ReportFetcher

from ...utils.test import UDSTestCase

logger: logging.Logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
]

PNG_MAGIC: bytes = b"\x89PNG\r\n\x1a\n"


class ReportFetcherTest(UDSTestCase):
    def test_stock_url_fetches_stock_image(self) -> None:
        fetcher = _ReportFetcher()
        response = fetcher.fetch("stock://logo-512.png")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.get("Content-Type"), "image/png")  # pyright: ignore[reportUnknownMemberType]
        self.assertTrue(response.read().startswith(PNG_MAGIC))  # pyright: ignore[reportUnknownMemberType]

    def test_image_url_returns_provided_image(self) -> None:
        fetcher = _ReportFetcher({"logo": b"FAKEIMG"})
        response = fetcher.fetch("image://logo")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.get("Content-Type"), "image/png")  # pyright: ignore[reportUnknownMemberType]
        self.assertEqual(response.read(), b"FAKEIMG")  # pyright: ignore[reportUnknownMemberType]

    def test_image_url_missing_returns_empty(self) -> None:
        fetcher = _ReportFetcher()
        response = fetcher.fetch("image://nope")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.get("Content-Type"), "image/png")  # pyright: ignore[reportUnknownMemberType]
        self.assertEqual(response.read(), b"")  # pyright: ignore[reportUnknownMemberType]

    def test_other_urls_delegate_to_default_fetcher(self) -> None:
        fetcher = _ReportFetcher()
        # data: URLs are handled by the default fetcher, no network needed
        response = fetcher.fetch("data:image/png;base64,iVBORw0KGgo=")
        self.assertEqual(response.headers.get("Content-Type"), "image/png")  # pyright: ignore[reportUnknownMemberType]
        self.assertEqual(response.read(), PNG_MAGIC)  # pyright: ignore[reportUnknownMemberType]


class ReportPdfTest(UDSTestCase):
    def test_as_pdf_returns_valid_pdf(self) -> None:
        pdf = Report.as_pdf("<html><body><h1>Hi</h1></body></html>")
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 1000)

    def test_as_pdf_with_stock_image(self) -> None:
        html = '<html><body><img src="stock://logo-512.png" /></body></html>'
        pdf = Report.as_pdf(html)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 1000)

    def test_as_pdf_with_inline_image(self) -> None:
        image_path = stock.get_stock_image_path("logo-512.png")
        with open(image_path, "rb") as f:
            image = f.read()
        html = '<html><body><img src="image://logo" /></body></html>'
        pdf = Report.as_pdf(html, images={"logo": image})
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 1000)
