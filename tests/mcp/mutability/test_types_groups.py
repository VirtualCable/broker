"""``group.update``: registration, discovery, CAS and execution shape."""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_types, get as registry_get
from uds.mutability.types.groups import GroupUpdate
from uds.REST.methods.users_groups import Groups

from tests.fixtures.authenticators import create_db_authenticator, create_db_groups
from tests.mcp.mutability._helpers import FlowTestCase, make_request


class GroupUpdateRegistryTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("group.update")
        self.assertIs(found, GroupUpdate)
        self.assertIn("group.update", [t.type_id for t in all_types()])

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            GroupUpdate().resolve_target("00000000-0000-0000-0000-000000000000")


class GroupUpdateFieldsTest(FlowTestCase):
    def test_only_mutable_fields_are_proposable(self) -> None:
        defs = GroupUpdate().field_definitions("group")
        names = [d["name"] for d in defs]

        # name and type are required context on the PUT but immutable
        self.assertEqual(names, ["comments", "state", "skip_mfa", "meta_if_any"])

    def test_snapshot_includes_context_and_fingerprint_tracks_changes(self) -> None:
        authenticator = create_db_authenticator()
        group = create_db_groups(authenticator, 1)[0]
        action_type = GroupUpdate()

        snapshot = action_type.snapshot_values(group, action_type.etag_fields("group"))
        # Context travels in every PUT payload...
        self.assertEqual(snapshot["name"], group.name)
        self.assertEqual(snapshot["type"], "normal")
        # ...but the proposal cannot touch it
        errors = action_type.validate_values("group", {"name": "renamed"})
        self.assertTrue(any("unknown fields" in e for e in errors))

        base = action_type.fingerprint(group)
        self.assertEqual(base, action_type.fingerprint(group))

        group.state = "I"
        group.save(update_fields=["state"])
        self.assertNotEqual(base, action_type.fingerprint(group))


class GroupUpdateExecuteTest(FlowTestCase):
    def test_execute_merges_over_stored_values_under_the_derived_parent(self) -> None:
        authenticator = create_db_authenticator()
        group = create_db_groups(authenticator, 1)[0]
        action = self._action(
            self._flow(),
            action_type="group.update",
            target_uuid=group.uuid,
            values={"comments": "Changed comment"},
            base_values={"comments": group.comments},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types.groups.RestProxy._execute_sync") as execute_sync:
            execute_sync.return_value = "done"
            summary = async_to_sync(GroupUpdate().execute)(action, request=make_request())

        self.assertIn("updated", summary)
        target = execute_sync.call_args[0][0]
        self.assertIs(target.handler, Groups)
        self.assertEqual(target.path, "authenticators/{uuid}/groups")
        params: dict[str, typing.Any] = execute_sync.call_args[0][2]
        # Proposed value wins; immutable context and untouched columns
        # keep stored values
        self.assertEqual(params["comments"], "Changed comment")
        self.assertEqual(params["name"], group.name)
        self.assertEqual(params["type"], "normal")
        self.assertEqual(params["state"], group.state)
        self.assertIn("skip_mfa", params)
        self.assertIn("meta_if_any", params)
        # Parent authenticator derived from the manager FK
        self.assertEqual(execute_sync.call_args[0][3], authenticator.uuid)

    def test_meta_group_sends_the_meta_type_marker(self) -> None:
        authenticator = create_db_authenticator()
        meta_group = authenticator.groups.create(name="meta group", is_meta=True, meta_if_any=True)
        action = self._action(
            self._flow(),
            action_type="group.update",
            target_uuid=meta_group.uuid,
            values={"meta_if_any": False},
            base_values={"meta_if_any": True},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types.groups.RestProxy._execute_sync") as execute_sync:
            execute_sync.return_value = "done"
            async_to_sync(GroupUpdate().execute)(action, request=make_request())

        params: dict[str, typing.Any] = execute_sync.call_args[0][2]
        self.assertEqual(params["type"], "meta")
        self.assertEqual(params["meta_if_any"], False)
