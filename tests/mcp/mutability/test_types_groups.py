"""``group.update``: registration, discovery, CAS and execution shape."""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.base import ActionOperation, StalePolicy
from uds.mutability.types.authenticators.group import GroupUpdate
from uds.REST.methods.users_groups import Groups

from tests.fixtures.authenticators import create_db_authenticator, create_db_groups
from tests.mcp.mutability._helpers import FlowTestCase, build_action, make_request


class GroupUpdateRegistryTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("group.update")
        assert found is not None
        self.assertIs(type(found()), GroupUpdate)
        self.assertIn("group.update", all_type_ids())

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

        with mock.patch("uds.mutability.types.authenticators.group.RestProxy._execute_sync") as execute_sync:
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

        with mock.patch("uds.mutability.types.authenticators.group.RestProxy._execute_sync") as execute_sync:
            execute_sync.return_value = "done"
            async_to_sync(GroupUpdate().execute)(action, request=make_request())

        params: dict[str, typing.Any] = execute_sync.call_args[0][2]
        self.assertEqual(params["type"], "meta")
        self.assertEqual(params["meta_if_any"], False)


class GroupCreateDeleteTest(FlowTestCase):
    """group.create / group.delete: detail creation and removal."""

    def test_supported_operations_derive_from_hooks(self) -> None:
        self.assertEqual(
            GroupUpdate.supported_operations(),
            frozenset({ActionOperation.UPDATE, ActionOperation.CREATE, ActionOperation.DELETE}),
        )

    def test_creation_targets_the_parent_authenticator(self) -> None:
        self.assertTrue(GroupUpdate.create_needs_parent)
        self.assertEqual(GroupUpdate(ActionOperation.CREATE).full_id, "group.create")
        delete = GroupUpdate(ActionOperation.DELETE)
        self.assertEqual(delete.full_id, "group.delete")
        self.assertEqual(delete.get_stale_policy(), StalePolicy.DENY)

    def test_create_execution_posts_a_normal_group_by_default(self) -> None:
        authenticator = create_db_authenticator()
        create = GroupUpdate(ActionOperation.CREATE)
        action = build_action(
            action_type="group.create",
            target_uuid=authenticator.uuid,
            values={"name": "new-group"},
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy._execute_sync") as execute_sync:
            execute_sync.return_value = {"id": "grp-uuid"}
            summary = async_to_sync(create.execute)(action, request=make_request())
            target, _request, params, parent_uuid = execute_sync.call_args[0]
            self.assertEqual(
                (target.handler, target.method.value, target.args),
                (Groups, "POST", ()),
            )
            self.assertEqual(parent_uuid, authenticator.uuid)
            self.assertEqual(params["name"], "new-group")
            self.assertEqual(params["comments"], "")
            self.assertEqual(params["state"], "A")
            # The handler requires the type marker: "normal" unless meta
            self.assertEqual(params["type"], "normal")
            self.assertNotIn("groups", params)
        self.assertIn("new-group", summary)
        self.assertIn("grp-uuid", summary)

    def test_create_execution_maps_meta_groups(self) -> None:
        authenticator = create_db_authenticator()
        create = GroupUpdate(ActionOperation.CREATE)
        action = build_action(
            action_type="group.create",
            target_uuid=authenticator.uuid,
            values={"name": "meta-group", "is_meta": True, "groups": "aaa , bbb"},
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy._execute_sync") as execute_sync:
            execute_sync.return_value = {"id": "grp-uuid"}
            async_to_sync(create.execute)(action, request=make_request())
            params = execute_sync.call_args[0][2]
            self.assertEqual(params["type"], "meta")
            self.assertEqual(params["groups"], ["aaa", "bbb"])

    def test_delete_execution_removes_with_parent_derived(self) -> None:
        authenticator = create_db_authenticator()
        group = create_db_groups(authenticator, 1)[0]
        delete = GroupUpdate(ActionOperation.DELETE)
        action = build_action(
            action_type="group.delete",
            target_uuid=group.uuid,
            values={},
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy._execute_sync") as execute_sync:
            execute_sync.return_value = None
            summary = async_to_sync(delete.execute)(action, request=make_request())
            target, _request, params, parent_uuid = execute_sync.call_args[0]
            self.assertEqual(
                (target.handler, target.method.value, target.args),
                (Groups, "DELETE", (group.uuid,)),
            )
            self.assertEqual(params, {})
            self.assertEqual(parent_uuid, authenticator.uuid)
        self.assertIn(group.name, summary)
        self.assertIn("deleted", summary)
