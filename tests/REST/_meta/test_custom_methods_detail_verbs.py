"""Functional tests for the multi-verb CustomMethod dispatch contract on DetailHandlers.

`test_custom_methods.py` already covers the master-side contract and enumerates
every custom method in the codebase, but it does not exercise the dispatch
end-to-end for a single Detail endpoint that serves multiple HTTP verbs.

This module freezes the behaviour of a Detail custom method declared twice
on the same URL under different verbs (``POST`` and ``DELETE`` here), which
is the pattern adopted by ``Users.token`` and any future per-item mutator
exposed to clients that prefer REST semantics over action-based paths.

The tests use the existing ``Users.token`` endpoint instead of injecting
synthetic handlers, so they exercise the full ``REST.Dispatcher`` →
``Authenticators.process_detail`` → ``Users.token`` pipeline.
"""

import typing
from unittest.mock import patch

from uds.REST import Handler

from tests.utils import rest
from uds.core import types


class DetailMultiVerbCustomMethodTest(rest.test.RESTTestCase):
    """Freeze the multi-verb CustomMethod contract on Detail handlers.

    The tests assert:

    1. ``Users.token`` accepts POST and DELETE on the same URL and
       dispatches them through the same function with the right verb.
    2. POST and DELETE both reach the same dispatcher with the right
       verb, without conflict or shadowing.
    3. GET (legacy COMPAT bridge) reaches POST-declared custom methods
       with deprecation headers.
    4. In NO_COMPAT mode, GET on a POST-only custom method returns 410.
    5. A verb that was never declared (PUT) is rejected by the dispatcher.
    6. Repeated verbs do not leak state between requests.
    """

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    # ------------------------------------------------------------------
    # T1 — POST and DELETE reach the same dispatcher with the right verb
    # ------------------------------------------------------------------
    def test_post_and_delete_route_through_same_dispatcher(self) -> None:
        """Both verbs dispatch to ``token`` with the matching operation."""
        user = self.users[0]
        token_url = f"authenticators/{self.auth.uuid}/users/{user.uuid}/token"

        created = self.client.rest_post(token_url)
        self.assertEqual(created.status_code, 200, created.content)
        self.assertTrue(created.json()["token"].startswith("uat-"))

        deleted = self.client.rest_delete(token_url)
        self.assertEqual(deleted.status_code, 200, deleted.content)
        self.assertIsNone(deleted.json()["token"])
        self.assertIsNone(deleted.json()["token_hint"])

    # ------------------------------------------------------------------
    # T2 — GET in COMPAT mode still hits the POST-declared method
    # ------------------------------------------------------------------
    def test_get_in_compat_mode_hits_post_with_deprecation(self) -> None:
        """Legacy GET → POST bridge emits the deprecation header."""
        user = self.users[0]
        self.client.rest_post(f"authenticators/{self.auth.uuid}/users/{user.uuid}/token")

        response = self.client.rest_get(f"authenticators/{self.auth.uuid}/users/{user.uuid}/token")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("Deprecation", response)

    # ------------------------------------------------------------------
    # T3 — GET in NO_COMPAT mode returns 410 Gone for a POST custom method
    # ------------------------------------------------------------------
    def test_get_in_no_compat_returns_gone(self) -> None:
        """NO_COMPAT rejects the legacy GET → POST bridge with HTTP 410.

        ``clean_related`` is a POST-only ``Users`` custom method, so we
        hit it via GET on the master-detail endpoint and expect 410.
        """
        user = self.users[0]
        url = f"authenticators/{self.auth.uuid}/users/{user.uuid}/clean_related"
        with patch.object(Handler, "api_compat", return_value=types.rest.ApiCompat.NO_COMPAT):
            response = self.client.rest_get(url)

        self.assertEqual(response.status_code, 410, response.content)

    # ------------------------------------------------------------------
    # T4 — PUT never collides with POST/DELETE on the same name
    # ------------------------------------------------------------------
    def test_put_on_token_returns_invalid_method(self) -> None:
        """A verb that was never declared is rejected by the dispatcher."""
        user = self.users[0]
        self.client.rest_post(f"authenticators/{self.auth.uuid}/users/{user.uuid}/token")
        response = self.client.rest_put(f"authenticators/{self.auth.uuid}/users/{user.uuid}/token")
        self.assertIn(response.status_code, (400, 405), response.content)

    # ------------------------------------------------------------------
    # T5 — POST → DELETE → POST round-trip keeps dispatcher state clean
    # ------------------------------------------------------------------
    def test_verb_round_trip_is_isolated(self) -> None:
        """Repeated verbs do not leak state between requests."""
        user = self.users[0]
        token_url = f"authenticators/{self.auth.uuid}/users/{user.uuid}/token"

        # State starts empty: POST creates a token (200).
        first_post = self.client.rest_post(token_url)
        self.assertEqual(first_post.status_code, 200, first_post.content)

        # DELETE clears it (200, body returns nulls).
        first_delete = self.client.rest_delete(token_url)
        self.assertEqual(first_delete.status_code, 200, first_delete.content)
        self.assertIsNone(first_delete.json()["token"])

        # After DELETE, POST can issue a fresh token again (state was cleared).
        second_post = self.client.rest_post(token_url)
        self.assertEqual(second_post.status_code, 200, second_post.content)
        self.assertNotEqual(
            second_post.json()["token_hint"],
            first_post.json()["token_hint"],
            "Rotating the token must produce a different hint",
        )

        # Subsequent POST without DELETE must reject with 400.
        repeat_post = self.client.rest_post(token_url)
        self.assertEqual(repeat_post.status_code, 400, repeat_post.content)

        # And DELETE clears it again.
        second_delete = self.client.rest_delete(token_url)
        self.assertEqual(second_delete.status_code, 200, second_delete.content)
        self.assertIsNone(second_delete.json()["token"])

    # ------------------------------------------------------------------
    # T6 — DELETE is wired through the dispatcher, not the item-delete path
    # ------------------------------------------------------------------
    def test_delete_does_not_remove_the_user(self) -> None:
        """DELETE /users/{user}/token revokes the token, not the user."""
        user = self.users[0]
        token_url = f"authenticators/{self.auth.uuid}/users/{user.uuid}/token"
        self.client.rest_post(token_url)

        self.client.rest_delete(token_url)

        # The user is still queryable after a token DELETE.
        response = self.client.rest_get(f"authenticators/{self.auth.uuid}/users/{user.uuid}")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["name"], user.name)
