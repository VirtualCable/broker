"""provider.update action type: discovery, validation, snapshots and CAS."""

import asyncio
import typing
from unittest import mock

from uds.REST.methods.providers import Providers
from uds.mutability.types.providers import ProviderUpdate

from tests.fixtures.services import create_db_provider
from tests.mcp.mutability._helpers import build_action
from tests.utils import rest

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


class ProviderUpdateTypeTest(rest.test.RESTTestCase):
    """Type behavior: discovery, validation, snapshots and CAS."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.provider = create_db_provider()
        self.action_type = ProviderUpdate()

    def test_field_definitions_cover_top_level_fields(self) -> None:
        defs = self.action_type.field_definitions(self.provider.data_type)
        names = [d["name"] for d in defs]
        for expected in ("name", "comments", "tags"):
            self.assertIn(expected, names)
        for definition in defs:
            self.assertIn("secret", definition)
            self.assertIn("type", definition)

    def test_flatten_values(self) -> None:
        flat = self.action_type.flatten_values(
            {"name": "n", "instance": {"host": "h", "port": 1}, "tags": ["t"]}
        )
        self.assertEqual(flat, {"name": "n", "tags": ["t"], "instance.host": "h", "instance.port": 1})

    def test_validate_values_accepts_known_fields(self) -> None:
        errors = self.action_type.validate_values(self.provider.data_type, {"name": "x", "comments": "y"})
        self.assertEqual(errors, [])

    def test_validate_values_rejects_unknown_fields_with_accepted_list(self) -> None:
        errors = self.action_type.validate_values(self.provider.data_type, {"zzz": 1})
        self.assertEqual(len(errors), 1)
        self.assertIn("zzz", errors[0])
        self.assertIn("accepted", errors[0])
        self.assertIn("name", errors[0])

    def test_snapshot_values_reads_current_state(self) -> None:
        snapshot = self.action_type.snapshot_values(self.provider, ["name", "comments"])
        self.assertEqual(snapshot["name"], self.provider.name)
        self.assertEqual(snapshot["comments"], self.provider.comments)

    def test_snapshot_and_fingerprint_are_stable(self) -> None:
        values = {"name": "whatever"}
        base_values, base_etag = self.action_type.snapshot_and_fingerprint(self.provider, values)
        self.assertEqual(base_values, {"name": self.provider.name})
        _, base_etag_again = self.action_type.snapshot_and_fingerprint(self.provider, values)
        self.assertEqual(base_etag, base_etag_again)

    def test_diff_detects_stale_base_and_changed_item(self) -> None:
        values = {"name": "proposed name"}
        base_values, base_etag = self.action_type.snapshot_and_fingerprint(self.provider, values)
        action = build_action(
            target_uuid=self.provider.uuid,
            values=values,
            base_values=base_values,
            base_etag=base_etag,
        )

        # Untouched target: no staleness at all
        diff = self.action_type.diff(action)
        self.assertEqual(diff["stale_fields"], [])
        self.assertFalse(diff["item_changed"])

        # Someone changed the field the proposal is based on: CAS hard stop
        self.provider.name = "changed by a human"
        self.provider.save()
        diff = self.action_type.diff(action)
        self.assertIn("name", diff["stale_fields"])
        self.assertTrue(diff["item_changed"])

    def test_describe_masks_secrets(self) -> None:
        class _SecretfulProviderUpdate(ProviderUpdate):
            @typing.override
            def secret_names(self, for_type: str, target: typing.Any = None) -> set[str]:
                return {"name"}

        action = build_action(
            target_uuid=self.provider.uuid,
            values={"name": "new-secret-value", "comments": "visible"},
            base_values={"name": "old", "comments": "old"},
            base_etag="x",
        )
        described = _SecretfulProviderUpdate().describe(action)
        self.assertEqual(described["changes"]["name"], "(redacted)")
        self.assertEqual(described["changes"]["comments"], "visible")
        self.assertEqual(described["secrets_changed"], ["name"])

    def test_execute_builds_canonical_put(self) -> None:
        action = build_action(
            target_uuid=self.provider.uuid,
            values={"name": "new-name", "instance": {"host": "h"}},
            base_values={"name": "old", "instance.host": "old"},
            base_etag="x",
        )
        request = mock.MagicMock()
        with (
            mock.patch("uds.mutability.types.providers.RestProxy") as proxy_cls,
            mock.patch.object(self.action_type, "resolve_target", return_value=self.provider),
        ):
            proxy_cls.return_value.execute = mock.AsyncMock()
            asyncio.run(self.action_type.execute(action, request))
            proxy_cls.return_value.execute.assert_called_once()
            target, called_request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual(
                (target.handler, target.method.value, target.args), (Providers, "PUT", (self.provider.uuid,))
            )
            self.assertEqual(called_request, request)
            self.assertEqual(params, {"name": "new-name", "instance": {"host": "h"}})
