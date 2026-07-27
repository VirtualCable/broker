# -*- coding: utf-8 -*-
#
# Copyright (c) 2025 Virtual Cable S.L.U.
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
Integration tests for RFC 7232 ETag / If-Match optimistic locking.

These tests exercise the *real* REST pipeline (dispatcher -> Handler subclass ->
DB) for both:

* a master endpoint  (``ModelHandler``): ``GET/PUT /providers/<uuid>``
* a detail endpoint (``DetailHandler``): ``GET/PUT /providers/<uuid>/services/<uuid>``

The ``TestProvider`` / ``TestService`` fixtures are reused as the concrete
provider / service under test. Only the HTTP-level precondition behaviour is
verified here; the helper unit tests live in
``tests/REST/_meta/test_etag_preconditions.py``.
"""

import typing

from uds import models

from ....fixtures import services as services_fixtures
from ....utils import rest

# ``data_type`` for the built-in ``TestProvider`` / ``TestServiceCache`` types.
_TEST_PROVIDER_TYPE: typing.Final[str] = "TestProvider"
_TEST_SERVICE_TYPE: typing.Final[str] = "TestService1"


def _etag_inner(etag: str) -> str:
    """Return the inner digest of ``etag`` (stripping wrapping quotes if any)."""
    if etag.startswith('"') and etag.endswith('"'):
        return etag[1:-1]
    return etag


class ProviderEtagIntegrationTest(rest.test.RESTTestCase):
    """Optimistic locking on the master endpoint ``Providers``."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        # Reset any leftover precondition headers from sibling tests.
        self.client.uds_headers.pop("If-Match", None)
        self.client.uds_headers.pop("If-None-Match", None)
        # Use a dedicated, service-less provider so PUT-update succeeds without
        # the extra configuration ``TestProvider`` needs once services exist.
        create_payload: dict[str, typing.Any] = {
            "name": "etag-master-provider",
            "comments": "created by ETag integration test",
            "data_type": _TEST_PROVIDER_TYPE,
            "tags": [],
        }
        create_response = self.client.rest_put("providers", data=create_payload)
        self.assertEqual(
            create_response.status_code,
            200,
            f"create provider must succeed, got {create_response.content!r}",
        )
        self.test_provider_uuid: str = create_response.json()["id"]
        self.test_provider_url: str = f"providers/{self.test_provider_uuid}"

    @typing.override
    def tearDown(self) -> None:
        # Clean up the dedicated provider so other tests aren't affected.
        try:
            self.client.rest_delete(self.test_provider_url)
        except Exception:  # noqa: BLE001
            pass
        super().tearDown()

    # -- GET emits ETag -------------------------------------------------

    def test_get_emits_etag_header(self) -> None:
        """GET on a master resource must include an ETag response header."""
        response = self.client.rest_get(self.test_provider_url)
        self.assertEqual(response.status_code, 200)
        etag = response.headers.get("ETag") or response.headers.get("etag")
        if etag is None:
            self.fail("GET /providers/<uuid> must emit ETag")

        self.assertTrue(
            len(_etag_inner(etag)) >= 16,
            f"ETag inner must be a non-trivial digest, got {etag!r}",
        )

    # -- PUT update path -------------------------------------------------

    def test_put_with_matching_if_match_succeeds(self) -> None:
        """PUT with the current ETag in If-Match must accept the update."""
        get_response = self.client.rest_get(self.test_provider_url)
        self.assertEqual(get_response.status_code, 200)
        etag = get_response.headers.get("ETag") or get_response.headers.get("etag")
        if etag is None:
            self.fail("GET /providers/<uuid> must emit ETag")

        payload: dict[str, typing.Any] = {
            "name": "etag-master-provider (after update)",
            "comments": "etag ok",
            "data_type": _TEST_PROVIDER_TYPE,
            "tags": [],
        }
        self.client.add_header("If-Match", etag)
        try:
            put_response = self.client.rest_put(self.test_provider_url, data=payload)
        finally:
            self.client.uds_headers.pop("If-Match", None)
        self.assertEqual(
            put_response.status_code,
            200,
            f"PUT with matching If-Match must succeed, got {put_response.content!r}",
        )

    def test_put_with_stale_if_match_returns_412(self) -> None:
        """PUT with a stale ETag in If-Match must return 412 Precondition Failed."""
        get_response = self.client.rest_get(self.test_provider_url)
        self.assertEqual(get_response.status_code, 200)
        current_etag = get_response.headers.get("ETag") or get_response.headers.get("etag")
        if current_etag is None:
            self.fail("GET /providers/<uuid> must emit ETag")

        # Force the etag to drift by issuing one PUT (with no precondition,
        # so the update succeeds and the etag changes).
        first_payload: dict[str, typing.Any] = {
            "name": "etag-master-provider (first update)",
            "comments": "etag drift",
            "data_type": _TEST_PROVIDER_TYPE,
            "tags": [],
        }
        self.client.uds_headers.pop("If-Match", None)
        first_response = self.client.rest_put(self.test_provider_url, data=first_payload)
        self.assertEqual(
            first_response.status_code,
            200,
            f"seeding PUT must succeed, got {first_response.content!r}",
        )

        # Now try to PUT with the *original* etag -> must be rejected.
        payload: dict[str, typing.Any] = {
            "name": "etag-master-provider (second update)",
            "comments": "etag stale",
            "data_type": _TEST_PROVIDER_TYPE,
            "tags": [],
        }
        self.client.add_header("If-Match", current_etag)
        try:
            response = self.client.rest_put(self.test_provider_url, data=payload)
        finally:
            self.client.uds_headers.pop("If-Match", None)
        self.assertEqual(
            response.status_code,
            412,
            f"PUT with stale If-Match must return 412, got {response.status_code} body={response.content!r}",
        )

    def test_put_without_if_match_is_allowed(self) -> None:
        """Per RFC 7232 preconditions are optional: PUT without If-Match must succeed."""
        payload: dict[str, typing.Any] = {
            "name": "etag-master-provider (no precondition)",
            "comments": "no if-match",
            "data_type": _TEST_PROVIDER_TYPE,
            "tags": [],
        }
        self.client.uds_headers.pop("If-Match", None)
        response = self.client.rest_put(self.test_provider_url, data=payload)
        self.assertEqual(
            response.status_code,
            200,
            f"PUT without If-Match must succeed, got {response.content!r}",
        )


class ServiceEtagIntegrationTest(rest.test.RESTTestCase):
    """Optimistic locking on the detail endpoint ``providers/<uuid>/services/<uuid>``."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        # Create a service under the auto-created provider so the detail endpoint
        # has a concrete item to operate on.
        self.service = services_fixtures.create_db_service(self.provider)
        # Reset any leftover precondition headers from sibling tests.
        self.client.uds_headers.pop("If-Match", None)
        self.client.uds_headers.pop("If-None-Match", None)

    def _detail_url(self) -> str:
        return f"providers/{self.provider.uuid}/services/{self.service.uuid}"

    def test_get_emits_etag_header(self) -> None:
        """GET on a detail resource must include an ETag response header."""
        response = self.client.rest_get(self._detail_url())
        self.assertEqual(response.status_code, 200)
        etag = response.headers.get("ETag") or response.headers.get("etag")
        if etag is None:
            self.fail("GET detail must emit ETag")
        self.assertTrue(
            len(_etag_inner(etag)) >= 16,
            f"ETag inner must be a non-trivial digest, got {etag!r}",
        )

    def test_put_with_matching_if_match_succeeds(self) -> None:
        get_response = self.client.rest_get(self._detail_url())
        self.assertEqual(get_response.status_code, 200)
        etag = get_response.headers.get("ETag") or get_response.headers.get("etag")
        if etag is None:
            self.fail("GET detail must emit ETag")

        payload: dict[str, typing.Any] = {
            "name": self.service.name + " (etag-test)",
            "comments": self.service.comments,
            "data_type": _TEST_SERVICE_TYPE,
            "max_services_count_type": 0,
            "tags": [],
        }
        self.client.add_header("If-Match", etag)
        try:
            response = self.client.rest_put(self._detail_url(), data=payload)
        finally:
            self.client.uds_headers.pop("If-Match", None)
        self.assertEqual(
            response.status_code,
            200,
            f"PUT detail with matching If-Match must succeed, got {response.content!r}",
        )

    def test_put_with_stale_if_match_returns_412(self) -> None:
        get_response = self.client.rest_get(self._detail_url())
        self.assertEqual(get_response.status_code, 200)
        current_etag = get_response.headers.get("ETag") or get_response.headers.get("etag")
        if current_etag is None:
            self.fail("GET detail must emit ETag")

        # Drift the etag by issuing one PUT with no precondition.
        first_payload: dict[str, typing.Any] = {
            "name": self.service.name + " (first update)",
            "comments": self.service.comments,
            "data_type": _TEST_SERVICE_TYPE,
            "max_services_count_type": 0,
            "tags": [],
        }
        self.client.uds_headers.pop("If-Match", None)
        first_response = self.client.rest_put(self._detail_url(), data=first_payload)
        self.assertEqual(
            first_response.status_code,
            200,
            f"seeding detail PUT must succeed, got {first_response.content!r}",
        )

        payload: dict[str, typing.Any] = {
            "name": self.service.name + " (second update)",
            "comments": self.service.comments,
            "data_type": _TEST_SERVICE_TYPE,
            "max_services_count_type": 0,
            "tags": [],
        }
        self.client.add_header("If-Match", current_etag)
        try:
            response = self.client.rest_put(self._detail_url(), data=payload)
        finally:
            self.client.uds_headers.pop("If-Match", None)
        self.assertEqual(
            response.status_code,
            412,
            f"PUT detail with stale If-Match must return 412, got {response.status_code} body={response.content!r}",
        )

    def test_put_without_if_match_is_allowed(self) -> None:
        payload: dict[str, typing.Any] = {
            "name": self.service.name + " (no precondition)",
            "comments": self.service.comments,
            "data_type": _TEST_SERVICE_TYPE,
            "max_services_count_type": 0,
            "tags": [],
        }
        self.client.uds_headers.pop("If-Match", None)
        response = self.client.rest_put(self._detail_url(), data=payload)
        self.assertEqual(
            response.status_code,
            200,
            f"PUT detail without If-Match must succeed, got {response.content!r}",
        )


class ProviderCreateIfMatchStarTest(rest.test.RESTTestCase):
    """``POST /providers`` (create) with ``If-Match: *`` must be rejected."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        self.client.uds_headers.pop("If-Match", None)
        self.client.uds_headers.pop("If-None-Match", None)

    def test_post_with_if_match_star_returns_412(self) -> None:
        payload: dict[str, typing.Any] = {
            "name": "etag-create-rejected",
            "comments": "must be rejected",
            "data_type": _TEST_PROVIDER_TYPE,
            "tags": [],
        }
        self.client.add_header("If-Match", "*")
        try:
            response = self.client.rest_put("providers", data=payload)
        finally:
            self.client.uds_headers.pop("If-Match", None)
        self.assertEqual(
            response.status_code,
            412,
            f"PUT /providers with If-Match: * must return 412, got {response.status_code} body={response.content!r}",
        )
        # And the provider must NOT have been created on disk.
        self.assertFalse(
            models.Provider.objects.filter(name="etag-create-rejected").exists(),
            "If-Match: * on create must not have persisted the new provider",
        )
