"""provider action types: discovery, validation, snapshots, CAS, create, delete."""

import asyncio
import typing
from unittest import mock

from uds.REST.methods.providers import Providers
from uds.mutability.base import CREATE_TARGET_UUID, ActionOperation, StalePolicy
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
        self.action_type = ProviderUpdate(ActionOperation.UPDATE)

    def test_field_definitions_cover_top_level_fields(self) -> None:
        defs = self.action_type.field_definitions(self.provider.data_type)
        names = [d["name"] for d in defs]
        for expected in ("name", "comments", "tags"):
            self.assertIn(expected, names)
        for definition in defs:
            self.assertIn("secret", definition)
            self.assertIn("type", definition)

    def test_from_instance_follows_the_builder_prefix(self) -> None:
        # Providers nests its module configuration under "instance."
        # (add_fields(parent="instance")): exactly those fields resolve to
        # instance values, the handler FIELDS_TO_SAVE columns do not
        for definition in self.action_type.field_definitions(self.provider.data_type):
            self.assertEqual(definition["from_instance"], definition["name"].startswith("instance."))

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
            # The PUT is form-shaped: the proposal travels merged over the
            # current top-level values, the instance config stays nested,
            # and the (never mutable) data_type is injected
            self.assertEqual(
                params,
                {
                    "name": "new-name",
                    "comments": self.provider.comments,
                    "tags": [],
                    "instance": {"host": "h"},
                    "data_type": self.provider.data_type,
                },
            )


class ProviderCreateDeleteTest(rest.test.RESTTestCase):
    """provider.create / provider.delete: the non-update verbs of the family."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.provider = create_db_provider()

    def test_supported_operations_derive_from_hooks(self) -> None:
        self.assertEqual(
            ProviderUpdate.supported_operations(),
            frozenset({ActionOperation.UPDATE, ActionOperation.CREATE, ActionOperation.DELETE}),
        )

    def test_delete_rides_the_strict_deny_policy(self) -> None:
        delete = ProviderUpdate(ActionOperation.DELETE)
        self.assertEqual(delete.full_id, "provider.delete")
        self.assertEqual(delete.get_stale_policy(), StalePolicy.DENY)

    def test_tool_text_phrases_each_verb(self) -> None:
        create = ProviderUpdate(ActionOperation.CREATE)
        delete = ProviderUpdate(ActionOperation.DELETE)
        self.assertIn("creating", create.tool_title().lower())
        self.assertIn("get_creatable_types", create.tool_description())
        self.assertIn("does NOT create anything", create.tool_description())
        self.assertIn("deleting", delete.tool_title().lower())
        self.assertIn("does NOT delete anything", delete.tool_description())

    def test_create_validation_rejects_unknown_fields_and_missing_type(self) -> None:
        create = ProviderUpdate(ActionOperation.CREATE)
        self.assertEqual(create.create_validate_values(self.provider.data_type, {"name": "x"}), [])
        self.assertTrue(create.create_validate_values("", {"name": "x"}))
        errors = create.create_validate_values(
            self.provider.data_type, {"data_type": self.provider.data_type, "zzz": 1}
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("zzz", errors[0])
        # the reserved key is consumed by the machinery, never "unknown"
        self.assertNotIn("data_type", errors[0])

    def test_create_execution_posts_the_form_shape(self) -> None:
        create = ProviderUpdate(ActionOperation.CREATE)
        action = build_action(
            action_type="provider.create",
            target_uuid=CREATE_TARGET_UUID,
            values={"data_type": self.provider.data_type, "name": "new prov", "host": "h"},
            base_values={},
            base_etag="",
        )
        request = mock.MagicMock()
        with mock.patch("uds.mutability.types.providers.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value={"id": "new-uuid"})
            summary = asyncio.run(create.execute(action, request))
            target, called_request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual((target.handler, target.method.value, target.args), (Providers, "POST", ()))
            self.assertEqual(called_request, request)
            # Form shape: top-level columns with safe defaults, bare config
            # names nested under "instance", data_type injected
            self.assertEqual(params["name"], "new prov")
            self.assertEqual(params["comments"], "")
            self.assertEqual(params["tags"], [])
            self.assertEqual(params["instance"], {"host": "h"})
            self.assertEqual(params["data_type"], self.provider.data_type)
        self.assertIn("new prov", summary)
        self.assertIn("new-uuid", summary)

    def test_delete_execution_sends_canonical_delete(self) -> None:
        delete = ProviderUpdate(ActionOperation.DELETE)
        action = build_action(
            action_type="provider.delete",
            target_uuid=self.provider.uuid,
            values={},
            base_values={},
            base_etag="",
        )
        request = mock.MagicMock()
        with (
            mock.patch("uds.mutability.types.providers.RestProxy") as proxy_cls,
            mock.patch.object(delete, "resolve_target", return_value=self.provider),
        ):
            proxy_cls.return_value.execute = mock.AsyncMock()
            summary = asyncio.run(delete.execute(action, request))
            target, _called_request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual(
                (target.handler, target.method.value, target.args),
                (Providers, "DELETE", (self.provider.uuid,)),
            )
            self.assertEqual(params, {})
        self.assertIn(self.provider.name, summary)
        self.assertIn("deleted", summary)

    def test_approval_snapshot_of_a_creation_needs_no_target(self) -> None:
        create = ProviderUpdate(ActionOperation.CREATE)
        action = build_action(
            action_type="provider.create",
            target_uuid=CREATE_TARGET_UUID,
            values={"data_type": self.provider.data_type, "name": "x"},
            base_values={},
            base_etag="",
        )
        snap = create.approval_snapshot(action)
        self.assertEqual(snap["target_name"], "new provider")
        self.assertEqual(snap["current_values"], {})
        names = [d["name"] for d in snap["fields"]]
        self.assertIn("name", names)

    def test_diff_of_a_creation_has_no_staleness(self) -> None:
        create = ProviderUpdate(ActionOperation.CREATE)
        action = build_action(
            action_type="provider.create",
            target_uuid=CREATE_TARGET_UUID,
            values={"data_type": self.provider.data_type, "name": "x"},
            base_values={},
            base_etag="",
        )
        diff = create.diff(action)
        self.assertEqual(diff["stale_fields"], [])
        self.assertFalse(diff["item_changed"])
        self.assertEqual(diff["proposed"], action.values)
