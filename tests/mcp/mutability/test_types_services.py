"""service.update action type: target-scoped discovery, CAS and parent permissions."""

import asyncio
import typing
from unittest import mock

from uds.REST.methods.providers import Providers
from uds.REST.methods.services import Services
from uds.mcp.rest_proxy import RestProxy
from uds.mutability.types_services import ServiceUpdate

from tests.fixtures.services import create_db_provider, create_db_service
from tests.mcp.mutability._helpers import build_action
from tests.utils import rest

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


class ServiceUpdateTypeTest(rest.test.RESTTestCase):
    """Detail-type behavior: target-scoped discovery, CAS and parent permissions."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.provider = create_db_provider()
        self.service = create_db_service(self.provider)
        self.action_type = ServiceUpdate()

    def test_field_definitions_cover_top_level_fields(self) -> None:
        defs = self.action_type.field_definitions(self.service.data_type, self.service)
        names = [d["name"] for d in defs]
        for expected in ("name", "comments", "tags", "max_services_count_type"):
            self.assertIn(expected, names)
        for definition in defs:
            self.assertIn("secret", definition)
            self.assertIn("type", definition)
            self.assertIn("from_instance", definition)

    def test_field_definitions_refuse_bare_type(self) -> None:
        # target_scoped_fields: the gui depends on the concrete service
        self.assertTrue(ServiceUpdate.target_scoped_fields)
        with self.assertRaises(ValueError):
            self.action_type.field_definitions(self.service.data_type)

    def test_permission_target_is_the_parent_provider(self) -> None:
        self.assertIs(self.action_type.permission_target(self.service), self.service.provider)

    def test_validate_values_accepts_known_fields(self) -> None:
        errors = self.action_type.validate_values(
            self.service.data_type, {"name": "x", "comments": "y"}, self.service
        )
        self.assertEqual(errors, [])

    def test_validate_values_rejects_unknown_fields_with_accepted_list(self) -> None:
        errors = self.action_type.validate_values(self.service.data_type, {"zzz": 1}, self.service)
        self.assertEqual(len(errors), 1)
        self.assertIn("zzz", errors[0])
        self.assertIn("accepted", errors[0])
        self.assertIn("name", errors[0])

    def test_snapshot_values_reads_current_state(self) -> None:
        snapshot = self.action_type.snapshot_values(self.service, ["name", "comments", "tags"])
        self.assertEqual(snapshot["name"], self.service.name)
        self.assertEqual(snapshot["comments"], self.service.comments)
        self.assertEqual(snapshot["tags"], [])

    def test_snapshot_and_fingerprint_are_stable(self) -> None:
        values = {"name": "whatever"}
        base_values, base_etag = self.action_type.snapshot_and_fingerprint(self.service, values)
        self.assertEqual(base_values, {"name": self.service.name})
        _, base_etag_again = self.action_type.snapshot_and_fingerprint(self.service, values)
        self.assertEqual(base_etag, base_etag_again)

    def test_diff_detects_stale_base_and_changed_item(self) -> None:
        values = {"name": "proposed name"}
        base_values, base_etag = self.action_type.snapshot_and_fingerprint(self.service, values)
        action = build_action(
            action_type="service.update",
            target_uuid=self.service.uuid,
            values=values,
            base_values=base_values,
            base_etag=base_etag,
        )

        diff = self.action_type.diff(action)
        self.assertEqual(diff["stale_fields"], [])
        self.assertFalse(diff["item_changed"])

        self.service.name = "changed by a human"
        self.service.save()
        diff = self.action_type.diff(action)
        self.assertIn("name", diff["stale_fields"])
        self.assertTrue(diff["item_changed"])

    def test_execute_builds_canonical_detail_put(self) -> None:
        action = build_action(
            action_type="service.update",
            target_uuid=self.service.uuid,
            values={"name": "new-name", "comments": "c"},
            base_values={"name": "old"},
            base_etag="x",
        )
        request = mock.MagicMock()
        with (
            mock.patch.object(RestProxy, "_execute_sync") as exec_sync,
            mock.patch.object(self.action_type, "resolve_target", return_value=self.service),
        ):
            exec_sync.return_value = None
            asyncio.run(self.action_type.execute(action, request))
            exec_sync.assert_called_once()
            target, called_request, params, parent_uuid = exec_sync.call_args[0]
            self.assertEqual(target.handler, Services)
            self.assertEqual(target.parent.handler, Providers)
            self.assertEqual(target.method.value, "PUT")
            self.assertEqual(target.args, (self.service.uuid,))
            self.assertEqual(called_request, request)
            self.assertEqual(parent_uuid, str(self.service.provider.uuid))
            # data_type is injected from the target (never mutable)
            self.assertEqual(params["data_type"], self.service.data_type)
            self.assertEqual(params["name"], "new-name")
