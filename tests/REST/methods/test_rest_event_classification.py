"""Unit tests for the REST audit -> event classification (_rest_event_type).

Covers every branch of the conversion applied on log_operation: canonical
CRUD shapes (master and detail) map to rest.create/update/delete, any other
mutation maps to rest.operation, and non model handlers / read methods
produce no event.
"""

import typing
from types import SimpleNamespace
from unittest import TestCase

from uds.REST.handlers import Handler
from uds.REST.log import _rest_event_type
from uds.core.types import notifiers


class _ModelHandler:
    """Handler double that looks like a ModelHandler (has MODEL/DETAIL/_args)."""

    MODEL: typing.ClassVar[type] = int

    def __init__(self, method: str, args: list[str], details: dict[str, typing.Any] | None = None) -> None:
        self.DETAIL = details or {}
        self._args = args
        self.request = SimpleNamespace(method=method)


class _PlainHandler:
    """Handler double without MODEL (auth, system, actor, ... handlers)."""

    def __init__(self, method: str, args: list[str]) -> None:
        self._args = args
        self.request = SimpleNamespace(method=method)


AUTH_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ITEM_UUID = "11111111-2222-3333-4444-555555555555"
DETAILS: typing.Final[dict[str, typing.Any]] = {"users": object}


class RestEventClassificationTest(TestCase):
    """Every branch of _rest_event_type, table tested."""

    def test_classification_table(self) -> None:
        cases: list[tuple[str, str, list[str], dict[str, typing.Any], notifiers.EventType]] = [
            # Master canonical shapes
            ("POST collection (create)", "POST", [], {}, notifiers.EventType.REST_CREATE),
            ("PUT item (update)", "PUT", [AUTH_UUID], {}, notifiers.EventType.REST_UPDATE),
            ("PATCH item (update)", "PATCH", [AUTH_UUID], {}, notifiers.EventType.REST_UPDATE),
            ("PUT empty (legacy create)", "PUT", [], {}, notifiers.EventType.REST_CREATE),
            ("PATCH empty (legacy create)", "PATCH", [], {}, notifiers.EventType.REST_CREATE),
            ("DELETE item (delete)", "DELETE", [AUTH_UUID], {}, notifiers.EventType.REST_DELETE),
            # Detail canonical shapes (args[1] is a detail name)
            ("POST detail create", "POST", [AUTH_UUID, "users"], DETAILS, notifiers.EventType.REST_CREATE),
            ("PUT detail legacy create", "PUT", [AUTH_UUID, "users"], DETAILS, notifiers.EventType.REST_CREATE),
            (
                "PATCH detail legacy create",
                "PATCH",
                [AUTH_UUID, "users"],
                DETAILS,
                notifiers.EventType.REST_CREATE,
            ),
            (
                "PUT detail item (update)",
                "PUT",
                [AUTH_UUID, "users", ITEM_UUID],
                DETAILS,
                notifiers.EventType.REST_UPDATE,
            ),
            (
                "DELETE detail item (delete)",
                "DELETE",
                [AUTH_UUID, "users", ITEM_UUID],
                DETAILS,
                notifiers.EventType.REST_DELETE,
            ),
            # Any other mutation -> rest.operation
            (
                "POST custom method (master)",
                "POST",
                [AUTH_UUID, "publish"],
                {},
                notifiers.EventType.REST_OPERATION,
            ),
            (
                "POST custom method (detail)",
                "POST",
                [AUTH_UUID, "users", ITEM_UUID, "token"],
                DETAILS,
                notifiers.EventType.REST_OPERATION,
            ),
            ("POST item (non canonical)", "POST", [AUTH_UUID], {}, notifiers.EventType.REST_OPERATION),
            ("PUT custom (master)", "PUT", [AUTH_UUID, "custom"], {}, notifiers.EventType.REST_OPERATION),
            ("DELETE custom (master)", "DELETE", [AUTH_UUID, "custom"], {}, notifiers.EventType.REST_OPERATION),
            (
                "DELETE custom (detail)",
                "DELETE",
                [AUTH_UUID, "users"],
                DETAILS,
                notifiers.EventType.REST_OPERATION,
            ),
        ]
        for name, method, args, details, expected in cases:
            with self.subTest(name):
                handler = typing.cast("Handler", _ModelHandler(method, args, details))
                self.assertEqual(_rest_event_type(handler), expected)

    def test_non_model_handlers_emit_no_event(self) -> None:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method):
                handler = typing.cast("Handler", _PlainHandler(method, ["whatever"]))
                self.assertIsNone(_rest_event_type(handler))

    def test_read_methods_emit_no_event(self) -> None:
        for method in ("GET", "HEAD", "OPTIONS"):
            for args in ([], [AUTH_UUID], [AUTH_UUID, "users"]):
                with self.subTest(f"{method} {args}"):
                    handler = typing.cast("Handler", _ModelHandler(method, args, DETAILS))
                    self.assertIsNone(_rest_event_type(handler))
