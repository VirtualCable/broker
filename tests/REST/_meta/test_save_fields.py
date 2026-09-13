"""
Unit tests for the ``FIELDS_TO_SAVE`` marker parser and its consumers.

``BaseModelHandler.parse_save_fields`` is the single interpretation point
for the ``"field:modifier"`` marker language shared by:

* ``fields_from_params`` (master/detail PUT paths),
* ``MasterHandler._item_with_etag`` (the CAS hash of save fields),
* ``ModuleUpdateActionType.params_columns`` (MCP mutability surface).

Before the parser existed, ``_item_with_etag`` hashed the *raw* marker
entries, and ``inmutables`` looked them up as literal dict keys: ``host:``
and ``port:0`` never matched the serialized item's ``host``/``port``, so
PUTs could move those columns without moving the ETag. The ETag tests
below pin the regression.
"""

import unittest

from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.permissions import PermissionType
from uds.REST.methods.tunnels_management import TunnelItem, Tunnels
from uds.REST.model import ModelHandler


class _StubHandler(ModelHandler[TunnelItem]):
    """Bare handler for ``fields_from_params``: it only reads ``self._params``."""

    PATH = "stub"
    NAME = "stub"


def _handler(params: dict[str, object]) -> _StubHandler:
    instance: _StubHandler = _StubHandler.__new__(_StubHandler)
    instance._params = params  # pyright: ignore[reportUnknownMemberType]
    return instance


# --------------------------------------------------------------------- #
# parse_save_fields                                                     #
# --------------------------------------------------------------------- #


class ParseSaveFieldsTestCase(unittest.TestCase):
    def test_plain_field_has_no_modifier(self) -> None:
        self.assertEqual(list(_StubHandler.parse_save_fields(["name"])), [("name", None)])

    def test_empty_modifier(self) -> None:
        self.assertEqual(list(_StubHandler.parse_save_fields(["host:"])), [("host", "")])

    def test_static_default_modifier(self) -> None:
        self.assertEqual(list(_StubHandler.parse_save_fields(["port:0"])), [("port", "0")])

    def test_skip_marker_modifier(self) -> None:
        self.assertEqual(list(_StubHandler.parse_save_fields(["mfa_id:_"])), [("mfa_id", "_")])

    def test_modifier_keeps_trailing_colons(self) -> None:
        """The split is on the *first* colon only (partition semantics).

        No declaration in the tree uses a double colon; keeping the
        modifier verbatim is deliberate, so a future ``"field:a:b"`` is
        reported whole instead of being silently truncated to ``"a"``.
        """
        self.assertEqual(list(_StubHandler.parse_save_fields(["field:a:b"])), [("field", "a:b")])

    def test_empty_list(self) -> None:
        self.assertEqual(list(_StubHandler.parse_save_fields([])), [])

    def test_real_tunnels_declaration(self) -> None:
        self.assertEqual(
            list(Tunnels.parse_save_fields(Tunnels.FIELDS_TO_SAVE)),
            [("name", None), ("comments", None), ("host", ""), ("port", "0")],
        )


# --------------------------------------------------------------------- #
# fields_from_params                                                    #
# --------------------------------------------------------------------- #


class FieldsFromParamsTestCase(unittest.TestCase):
    def test_plain_field_reads_raw_value(self) -> None:
        handler = _handler({"name": "abc"})
        self.assertEqual(handler.fields_from_params(["name"]), {"name": "abc"})

    def test_plain_field_missing_raises(self) -> None:
        handler = _handler({})
        with self.assertRaises(rest_exceptions.RequestError):
            handler.fields_from_params(["name"])

    def test_plain_field_uses_defaults_dict(self) -> None:
        handler = _handler({})
        self.assertEqual(handler.fields_from_params(["name"], defaults={"name": "d"}), {"name": "d"})

    def test_plain_field_value_not_stringified(self) -> None:
        handler = _handler({"port": 443})
        self.assertEqual(handler.fields_from_params(["port"]), {"port": 443})

    def test_marked_field_missing_uses_default_string(self) -> None:
        handler = _handler({})
        self.assertEqual(handler.fields_from_params(["port:0"]), {"port": "0"})

    def test_marked_field_missing_empty_default(self) -> None:
        handler = _handler({})
        self.assertEqual(handler.fields_from_params(["host:"]), {"host": ""})

    def test_marked_field_missing_skip_is_omitted(self) -> None:
        handler = _handler({})
        self.assertEqual(handler.fields_from_params(["mfa_id:_"]), {})

    def test_marked_field_present_is_stringified(self) -> None:
        handler = _handler({"port": 123})
        self.assertEqual(handler.fields_from_params(["port:0"]), {"port": "123"})

    def test_marked_field_present_skip_still_saved(self) -> None:
        handler = _handler({"mfa_id": "uuid-1"})
        self.assertEqual(handler.fields_from_params(["mfa_id:_"]), {"mfa_id": "uuid-1"})

    def test_mixed_declaration(self) -> None:
        handler = _handler({"name": "t", "comments": "d", "host": "1.2.3.4"})
        self.assertEqual(
            handler.fields_from_params(Tunnels.FIELDS_TO_SAVE),
            {"name": "t", "comments": "d", "host": "1.2.3.4", "port": "0"},
        )


# --------------------------------------------------------------------- #
# ETag regression (the bug the parser fixes)                            #
# --------------------------------------------------------------------- #


def _tunnel(host: str, port: int, name: str = "t", comments: str = "") -> TunnelItem:
    return TunnelItem(
        id="uuid",
        name=name,
        comments=comments,
        host=host,
        port=port,
        tags=[],
        transports_count=0,
        servers_count=0,
        permission=PermissionType.NONE,
    )


class EtagSaveFieldsTestCase(unittest.TestCase):
    def _parsed_fields(self) -> list[str]:
        return [name for name, _ in Tunnels.parse_save_fields(Tunnels.FIELDS_TO_SAVE)]

    def test_host_change_moves_etag(self) -> None:
        fields = self._parsed_fields()
        etag_a = _tunnel("10.0.0.1", 443).etag(*fields)
        etag_b = _tunnel("10.0.0.2", 443).etag(*fields)
        self.assertNotEqual(etag_a, etag_b)

    def test_port_change_moves_etag(self) -> None:
        fields = self._parsed_fields()
        etag_a = _tunnel("10.0.0.1", 443).etag(*fields)
        etag_b = _tunnel("10.0.0.1", 8443).etag(*fields)
        self.assertNotEqual(etag_a, etag_b)

    def test_raw_marker_entries_ignore_host_and_port(self) -> None:
        """Pins *why* parsing is required: the old hash used the raw list.

        With the un-parsed ``["name", "comments", "host:", "port:0"]``
        keys, ``inmutables`` looks up literal ``"host:"``/``"port:0"``
        in the serialized item, misses, and the etag is blind to both
        columns. If a future change ever feeds raw markers to ``etag()``
        again, this test documents the exact silent-corruption mode.
        """
        raw = list(Tunnels.FIELDS_TO_SAVE)
        etag_a = _tunnel("10.0.0.1", 443).etag(*raw)
        etag_b = _tunnel("10.0.0.2", 8443).etag(*raw)
        self.assertEqual(etag_a, etag_b)

    def test_stable_across_calls(self) -> None:
        fields = self._parsed_fields()
        item = _tunnel("h", 1, name="n")
        self.assertEqual(item.etag(*fields), _tunnel("h", 1, name="n").etag(*fields))
