"""Document how the REST dispatcher parses POST parameters.

Pins the current behaviour so future refactors can spot regressions:

- Empty body (``CONTENT_LENGTH`` 0 / missing): fall back to query string.
- Body decodes to a non-empty dict: use the body. Query is ignored.
- Body decodes to an empty dict (``{}``): fall back to query string too.
  This makes Angular ``http.post(url, {})`` (which serialises the empty
  object to the literal ``"{}"``) honour URL parameters.

Reference: ``processors.MarshallerProcessor.process_parameters``.
"""

from __future__ import annotations

import json
import typing

from uds import models
from uds.core.types.states import State

from tests.fixtures import services as services_fixtures
from tests.utils import rest


class PostBodyVsQueryTest(rest.test.RESTTestCase):
    """Pins how the dispatcher treats body vs query string on POST."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def _make_pool(self) -> models.ServicePool:
        service = services_fixtures.create_db_service(self.provider)
        osmanager = services_fixtures.create_db_osmanager()
        transport = services_fixtures.create_db_transport()
        return services_fixtures.create_db_servicepool(service, osmanager, self.groups, [transport])

    def test_get_returns_default(self) -> None:
        """GET /fallback_access returns the current value (default ALLOW)."""
        pool = self._make_pool()
        response = self.client.rest_get(f"servicespools/{pool.uuid}/fallback_access")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), State.ALLOW)

    def test_post_body_only_sets_fallback(self) -> None:
        """POST with a non-empty JSON body sets the fallback.

        ``rest_post(data={'fallback_access': ...})`` serialises to
        ``{"fallback_access": "DENY"}``; the body is used and the query
        string is ignored.
        """
        pool = self._make_pool()
        response = self.client.rest_post(
            f"servicespools/{pool.uuid}/fallback_access",
            data={"fallback_access": State.DENY},
        )
        pool.refresh_from_db()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(pool.fallbackAccess, State.DENY)

    def test_post_empty_body_falls_back_to_query(self) -> None:
        """POST with truly empty body + ``?fallback_access=DENY``: query wins."""
        pool = self._make_pool()
        response = self.client.post(
            f"/uds/rest/servicespools/{pool.uuid}/fallback_access?fallback_access={State.DENY}",
            data="",
            content_type="application/json",
        )
        pool.refresh_from_db()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(pool.fallbackAccess, State.DENY)

    def test_post_empty_dict_body_falls_back_to_query(self) -> None:
        """POST body ``{}`` (Angular default) + query: query wins.

        ``gui/admin invoke()`` POST helper sends ``http.post(url, {})``
        which serialises to the literal string ``"{}"``. After decode the
        body is an empty dict, so the dispatcher falls back to the query
        string. This is the fix that makes the admin GUI's
        ``setFallbackAccess`` POST work end-to-end.
        """
        pool = self._make_pool()
        response = self.client.post(
            f"/uds/rest/servicespools/{pool.uuid}/fallback_access?fallback_access={State.DENY}",
            data=json.dumps({}),
            content_type="application/json",
        )
        pool.refresh_from_db()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(pool.fallbackAccess, State.DENY)

    def test_post_non_empty_body_takes_precedence_over_query(self) -> None:
        """POST body={fallback_access: DENY} + ``?fallback_access=ALLOW``: body wins."""
        pool = self._make_pool()
        response = self.client.post(
            f"/uds/rest/servicespools/{pool.uuid}/fallback_access?fallback_access={State.ALLOW}",
            data=json.dumps({"fallback_access": State.DENY}),
            content_type="application/json",
        )
        pool.refresh_from_db()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(pool.fallbackAccess, State.DENY)

    def test_post_angular_invoke_repro(self) -> None:
        """Reproduce ``gui/admin invoke()`` end-to-end.

        ``invoke(method, params, 'POST')`` does::

            http.post(url + '?' + params, {}, { headers })

        URL has the query param, body is the literal ``{}``. The dispatcher
        sees the body decodes to an empty dict and falls back to the query
        string — the pool ends up at DENY.
        """
        pool = self._make_pool()
        response = self.client.post(
            f"/uds/rest/servicespools/{pool.uuid}/fallback_access?fallback_access={State.DENY}",
            data=json.dumps({}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        pool.refresh_from_db()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(pool.fallbackAccess, State.DENY)
