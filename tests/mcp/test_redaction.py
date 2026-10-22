"""Tests for the defensive redaction of MCP responses.

Author: Adolfo Gómez, dkmaster at dkmon dot com
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import typing
import unittest

from uds.mcp.redaction import REDACTED, SENSITIVE_FIELDS, redact


class RedactionTest(unittest.TestCase):
    """Redaction is key-name based (denylist), recursive and total on names."""

    def test_every_sensitive_name_is_redacted(self) -> None:
        """Each name on the list must be replaced, whatever the value."""
        for name in SENSITIVE_FIELDS:
            value = {name: "top-secret-value"}
            self.assertEqual(redact(value), {name: REDACTED})

    def test_sensitive_names_are_matched_case_insensitively(self) -> None:
        """Keys are matched case-insensitively (``Token``, ``PASSWORD``...)."""
        value = {"Token": "a", "PASSWORD": "b", "SeCrEt_KeY": "c"}
        self.assertEqual(redact(value), {"Token": REDACTED, "PASSWORD": REDACTED, "SeCrEt_KeY": REDACTED})

    def test_recursion_covers_nested_containers(self) -> None:
        """Nested mappings, lists and tuples are walked and redacted."""
        value: dict[str, typing.Any] = {
            "ok": {
                "nested": [
                    {"token": "x", "name": "kept"},
                    ("password", {"pair": "y"}),
                ]
            }
        }
        self.assertEqual(
            redact(value),
            {"ok": {"nested": [{"token": REDACTED, "name": "kept"}, ("password", {"pair": "y"})]}},
        )

    def test_dotted_module_fields_are_matched_by_their_last_segment(self) -> None:
        value = {"instance.password": "a", "instance.client_secret": "b", "instance.host": "kept"}
        self.assertEqual(
            redact(value),
            {"instance.password": REDACTED, "instance.client_secret": REDACTED, "instance.host": "kept"},
        )

    def test_flags_are_not_secrets(self) -> None:
        definition = {"name": "instance.password", "secret": True, "required": False}
        self.assertEqual(redact(definition), definition)
        self.assertEqual(redact({"secret": False, "token": None}), {"secret": False, "token": None})

    def test_structured_secret_values_are_masked_whole(self) -> None:
        value = {"instance.private_key": ["line1", "line2"], "credentials": {"user": "u", "pin": 1234}}
        self.assertEqual(redact(value), {"instance.private_key": REDACTED, "credentials": REDACTED})

    def test_last_segment_must_match_whole(self) -> None:
        value = {"instance.password_policy": "strict", "instance.passwordless": "yes", "password.hint": "kept"}
        self.assertEqual(redact(value), value)

    def test_last_segment_is_matched_case_insensitively_at_any_depth(self) -> None:
        self.assertEqual(
            redact({"Instance.PassWord": "a", "a.b.c.token": "b"}),
            {"Instance.PassWord": REDACTED, "a.b.c.token": REDACTED},
        )

    def test_dotted_keys_inside_sequences_are_redacted(self) -> None:
        value = {"items": [{"instance.client_secret": "s", "instance.host": "h"}, ({"x.api_key": "k"},)]}
        self.assertEqual(
            redact(value),
            {"items": [{"instance.client_secret": REDACTED, "instance.host": "h"}, ({"x.api_key": REDACTED},)]},
        )

    def test_extra_keys_are_matched_by_last_segment(self) -> None:
        self.assertEqual(redact({"instance.vcpass": "s"}, ["VCPASS"]), {"instance.vcpass": REDACTED})

    def test_innocuous_names_survive(self) -> None:
        """Ordinary fields are not touched, so data is not distorted."""
        value = {"name": "kept", "uuid": "abc", "id": 7, "count": 3}
        self.assertEqual(redact(value), value)

    def test_scalars_pass_through(self) -> None:
        """Scalar values (non-mapping, non-sequence) are returned untouched."""
        for scalar in ("str", 7, 3.5, True, None):
            self.assertEqual(redact(scalar), scalar)

    def test_extra_keys_are_unioned_with_global_denylist(self) -> None:
        """Per-call ``extra_keys`` add to (not replace) the global denylist.

        When no curated tool or resource declares fields, the function must
        behave exactly as before: only the global ``SENSITIVE_FIELDS`` list
        applies. When one does declare fields, those fields are redacted on
        top of the global list.
        """
        value = {"public": "ok", "service_inventory_token": "leaked", "token": "leaked"}
        # Extra key catches a field the global denylist would miss.
        self.assertEqual(
            redact(value, ("service_inventory_token",)),
            {"public": "ok", "service_inventory_token": REDACTED, "token": REDACTED},
        )
        # Empty / default ``extra_keys`` equals the historical behaviour.
        self.assertEqual(redact({"token": "x"}), {"token": REDACTED})
        self.assertEqual(redact({"token": "x"}, ()), {"token": REDACTED})
        # A non-string entry in ``extra_keys`` is ignored without crashing.
        self.assertEqual(
            redact({"service_inventory_token": "y"}, ("service_inventory_token", 42)),
            {"service_inventory_token": REDACTED},
        )


class ModuleSensitiveFieldsTest(unittest.TestCase):
    """The union of module-declared sensitive fields feeds the redaction."""

    def test_module_declared_passwords_are_redacted(self) -> None:
        """Fields declared as passwords by any module are redacted, even
        when their name is outside the global denylist (i.e. ``pin`` on the
        test transport)."""
        from tests.fixtures.services import ensure_test_modules_registered

        ensure_test_modules_registered()

        from uds.mcp.redaction import module_sensitive_fields

        module_sensitive_fields.cache_clear()  # Other tests may have warmed it earlier
        fields = module_sensitive_fields()
        self.assertIn("pin", fields)  # From TestTransport

        value = {"instance": {"test_url": "https://x", "pin": "1234", "name": "kept"}}
        redacted = redact(value, module_sensitive_fields())
        self.assertEqual(redacted["instance"]["pin"], REDACTED)
        self.assertEqual(redacted["instance"]["test_url"], "https://x")
        self.assertEqual(redacted["instance"]["name"], "kept")

    def test_a_declaration_widens_the_denylist_for_every_module(self) -> None:
        """One module declaring a name blanks that name on every other module's payload."""
        from tests.fixtures.services import ensure_test_modules_registered

        ensure_test_modules_registered()

        from uds.mcp.redaction import module_sensitive_fields

        module_sensitive_fields.cache_clear()
        unrelated = {"authenticator": {"name": "kept", "pin": "not a secret here"}}
        redacted = redact(unrelated, module_sensitive_fields())
        self.assertEqual(redacted["authenticator"]["pin"], REDACTED)
