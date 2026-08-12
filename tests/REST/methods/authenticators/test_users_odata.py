"""
Tests for OData ``$filter`` / ``$orderby`` error handling on model list
endpoints: invalid field names must yield a ``400`` client error instead of
an ``HTTP 500`` that leaks the model schema (``FieldError`` handling).
"""

import typing

from ....utils import rest


class UsersODataErrorHandlingTest(rest.test.RESTTestCase):
    """Invalid ``$filter``/``$orderby`` fields return 400; valid ones keep working."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def _users_url(self) -> str:
        return self.client.compose_rest_url(f"authenticators/{self.auth.uuid}/users")

    def test_invalid_filter_field_returns_400(self) -> None:
        """An unknown field in ``$filter`` must be a client error (400), not a 500."""
        response = self.client.get(self._users_url(), {"$filter": "contains(nonexistent_field, 'x')"})
        self.assertEqual(response.status_code, 400, response.content)

    def test_invalid_orderby_field_returns_400(self) -> None:
        """An unknown field in ``$orderby`` must be a client error (400), not a 500."""
        response = self.client.get(self._users_url(), {"$orderby": "nonexistent_field desc"})
        self.assertEqual(response.status_code, 400, response.content)

    def test_valid_filter_still_returns_200(self) -> None:
        """Valid ``$filter`` expressions keep working and filter results."""
        response = self.client.get(self._users_url(), {"$filter": "contains(name, 'user')"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json())

    def test_valid_orderby_still_returns_200(self) -> None:
        """Valid ``$orderby`` expressions keep working."""
        response = self.client.get(self._users_url(), {"$orderby": "name desc"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json())
