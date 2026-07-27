"""
Unit tests for ``Handler`` ETag / precondition helpers.

These tests exercise ``Handler.set_etag`` and
``Handler.check_if_match_header`` in isolation by short-circuiting
``Handler.__init__`` (which enforces authentication and role checks
irrelevant to header validation) and stubbing only what the helpers
actually touch: ``self._request`` (with a ``headers`` mapping) and
``self._headers`` (the response header accumulator).

RFC 7232 preconditions are **optional**: a request with no
``If-Match`` / ``If-None-Match`` headers always succeeds.
"""
# pylint: disable=protected-access

import unittest

from uds.core.exceptions.rest import PreconditionFailed
from uds.REST.handlers import Handler


class _FakeRequest:
    """Minimal stand-in for ``ExtendedHttpRequestWithUser``.

    Handler helpers read headers via ``self._request.headers.get(...)``,
    so this exposes a ``headers`` mapping (no ``.get`` proxy is needed).
    """

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers: dict[str, str] = dict(headers or {})


def _make_handler(
    request_headers: dict[str, str] | None = None,
) -> Handler:
    """Build a bare ``Handler`` instance skipping auth/role machinery."""
    instance: Handler = Handler.__new__(Handler)
    instance._request = _FakeRequest(request_headers)  # type: ignore[assignment]
    instance._headers = {}  # pyright: ignore[attr-defined]
    return instance


# --------------------------------------------------------------------- #
# set_etag                                                              #
# --------------------------------------------------------------------- #


class SetEtagTestCase(unittest.TestCase):
    """``Handler.set_etag`` adds a quoted ``ETag`` header."""

    def test_quotes_hex_digest(self) -> None:
        handler = _make_handler()
        handler.set_etag("abc123")
        assert handler._headers["ETag"] == '"abc123"'  # noqa: SLF001

    def test_each_call_overwrites_previous(self) -> None:
        handler = _make_handler()
        handler.set_etag("first")
        handler.set_etag("second")
        assert handler._headers["ETag"] == '"second"'  # noqa: SLF001


# --------------------------------------------------------------------- #
# check_if_match_header                                                 #
# --------------------------------------------------------------------- #


class CheckIfMatchNoHeadersTestCase(unittest.TestCase):
    """No precondition headers → no-op regardless of current ETag."""

    def test_create_path_accepts_when_no_headers(self) -> None:
        handler = _make_handler()
        handler.check_if_match_header(None)  # must not raise

    def test_update_path_accepts_when_no_headers(self) -> None:
        handler = _make_handler()
        handler.check_if_match_header("abc123")  # must not raise


class CheckIfMatchCreatePathTestCase(unittest.TestCase):
    """``current_etag is None`` (*create*) path.

    On create paths the helper ignores concrete If-Match / If-None-Match
    values (legacy GUI POST/PUT-create ships spurious ones) and only
    enforces ``If-Match: *``, which would imply "create only if not
    exists" and collides with the call site semantics. Result: a POST
    with a concrete If-Match is accepted, but a POST with ``If-Match: *``
    is rejected with 412.
    """

    def test_if_match_star_rejected(self) -> None:
        handler = _make_handler({"If-Match": "*"})
        with self.assertRaises(PreconditionFailed):
            handler.check_if_match_header(None)

    def test_if_match_concrete_value_accepted(self) -> None:
        handler = _make_handler({"If-Match": '"abc"'})
        handler.check_if_match_header(None)  # must not raise

    def test_if_match_quoted_hex_with_w_prefix_accepted(self) -> None:
        """Weak concrete If-Match on a create path is also ignored."""
        handler = _make_handler({"If-Match": 'W/"abc"'})
        handler.check_if_match_header(None)  # must not raise

    def test_if_none_match_star_accepted(self) -> None:
        """``If-None-Match: *`` is not enforced on the create branch."""
        handler = _make_handler({"If-None-Match": "*"})
        handler.check_if_match_header(None)  # must not raise

    def test_if_none_match_concrete_value_accepted(self) -> None:
        handler = _make_handler({"If-None-Match": '"abc"'})
        handler.check_if_match_header(None)  # must not raise


class CheckIfMatchIfNoneMatchUpdateTestCase(unittest.TestCase):
    """``If-None-Match`` on the *update* path.

    The semantics are reversed: ``If-None-Match: <x>`` wants the current
    state to be something different from ``x``.
    """

    def test_matching_value_rejected(self) -> None:
        handler = _make_handler({"If-None-Match": '"abc"'})
        with self.assertRaises(PreconditionFailed):
            handler.check_if_match_header("abc")

    def test_different_value_accepted(self) -> None:
        handler = _make_handler({"If-None-Match": '"abc"'})
        handler.check_if_match_header("def")  # must not raise

    def test_star_means_resource_must_not_exist_rejects_update(self) -> None:
        """``If-None-Match: *`` on update = "fail because resource exists"."""
        handler = _make_handler({"If-None-Match": "*"})
        with self.assertRaises(PreconditionFailed):
            handler.check_if_match_header("abc")

    def test_weak_etag_is_compared_by_value(self) -> None:
        """RFC 7232 §2.3.2 weak comparison.

        The validator strips the ``W/`` prefix and the surrounding quotes
        and compares raw values, so ``W/"abc"`` matches ``abc``.
        """
        handler = _make_handler({"If-None-Match": 'W/"abc"'})
        with self.assertRaises(PreconditionFailed):
            handler.check_if_match_header("abc")

    def test_weak_etag_unrelated_value_accepted(self) -> None:
        handler = _make_handler({"If-None-Match": 'W/"abc"'})
        handler.check_if_match_header("def")  # must not raise


class CheckIfMatchIfMatchUpdateTestCase(unittest.TestCase):
    """``If-Match`` on the *update* path."""

    def test_matching_value_accepted(self) -> None:
        handler = _make_handler({"If-Match": '"abc"'})
        handler.check_if_match_header("abc")  # must not raise

    def test_different_value_rejected(self) -> None:
        handler = _make_handler({"If-Match": '"abc"'})
        with self.assertRaises(PreconditionFailed):
            handler.check_if_match_header("def")

    def test_star_accepted_on_update(self) -> None:
        """``If-Match: *`` on update = "any current state is fine"."""
        handler = _make_handler({"If-Match": "*"})
        handler.check_if_match_header("abc")  # must not raise

    def test_weak_etag_compared_by_value(self) -> None:
        handler = _make_handler({"If-Match": 'W/"abc"'})
        handler.check_if_match_header("abc")  # must not raise

    def test_weak_etag_mismatch_rejected(self) -> None:
        handler = _make_handler({"If-Match": 'W/"abc"'})
        with self.assertRaises(PreconditionFailed):
            handler.check_if_match_header("def")


class CheckIfMatchBothHeadersTestCase(unittest.TestCase):
    """Both ``If-Match`` and ``If-None-Match`` set.

    Both are independently validated. The code raises from whichever
    violates first (in source order: ``If-None-Match``, then ``If-Match``).
    """

    def test_both_match_current_rejected(self) -> None:
        """Both match → ``If-None-Match`` rejects first."""
        handler = _make_handler({"If-Match": '"abc"', "If-None-Match": '"abc"'})
        with self.assertRaises(PreconditionFailed):
            handler.check_if_match_header("abc")

    def test_if_match_passes_if_none_match_mismatches(self) -> None:
        """If-Match value differs but If-None-Match passes, mismatch wins."""
        handler = _make_handler({"If-Match": '"xxx"', "If-None-Match": '"abc"'})
        with self.assertRaises(PreconditionFailed):
            handler.check_if_match_header("abc")

    def test_star_if_match_with_if_none_match_star_rejected(self) -> None:
        """``If-Match: *`` accepts anything but ``If-None-Match: *`` rejects
        update because resource exists. The If-None-Match wins.
        """
        handler = _make_handler({"If-Match": "*", "If-None-Match": "*"})
        with self.assertRaises(PreconditionFailed):
            handler.check_if_match_header("abc")
