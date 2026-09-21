"""``user.update`` / ``user.custom``: registration, discovery, CAS and execution shape."""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.base import ActionOperation, StalePolicy
from uds.mutability.types.authenticators.user import UserUpdate
from uds.REST.methods.users_groups import Users

from tests.fixtures.authenticators import create_db_authenticator, create_db_groups, create_db_users
from tests.fixtures.mfas import create_db_mfa
from tests.mcp.mutability._helpers import FlowTestCase, build_action, make_request


class UserUpdateRegistryTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("user.update")
        assert found is not None
        self.assertIs(type(found()), UserUpdate)
        self.assertIn("user.update", all_type_ids())

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            UserUpdate().resolve_target("00000000-0000-0000-0000-000000000000")


class UserUpdateFieldsTest(FlowTestCase):
    def test_field_definitions_and_write_only_password(self) -> None:
        defs = UserUpdate().field_definitions("user")
        by_name = {d["name"]: d for d in defs}

        for name in ("name", "real_name", "comments", "state", "mfa_data", "password", "groups"):
            self.assertIn(name, by_name)
        # Privilege escalation vectors are not proposable
        self.assertNotIn("staff_member", by_name)
        self.assertNotIn("is_admin", by_name)
        self.assertTrue(by_name["password"]["secret"])
        self.assertEqual([d["name"] for d in defs if d["secret"]], ["password"])

    def test_privileges_cannot_be_proposed(self) -> None:
        errors = UserUpdate().validate_values("user", {"is_admin": True})
        self.assertTrue(any("unknown fields" in e for e in errors))
        errors = UserUpdate().validate_values("user", {"staff_member": True})
        self.assertTrue(any("unknown fields" in e for e in errors))

    def test_permissions_are_inherited_from_the_authenticator(self) -> None:
        authenticator = create_db_authenticator()
        user = create_db_users(authenticator, 1)[0]
        self.assertIs(UserUpdate().permission_target(user), authenticator)

    def test_snapshot_excludes_password_and_fingerprint_tracks_changes(self) -> None:
        authenticator = create_db_authenticator()
        user = create_db_users(authenticator, 1)[0]
        action_type = UserUpdate()

        snapshot = action_type.snapshot_values(user, action_type.etag_fields("user"))
        self.assertEqual(snapshot["name"], user.name)
        self.assertNotIn("password", snapshot)

        base = action_type.fingerprint(user)
        self.assertEqual(base, action_type.fingerprint(user))

        user.comments = "changed"
        user.save(update_fields=["comments"])
        self.assertNotEqual(base, action_type.fingerprint(user))


class UserUpdateExecuteTest(FlowTestCase):
    def test_execute_merges_over_stored_values_under_the_derived_parent(self) -> None:
        authenticator = create_db_authenticator()
        user = create_db_users(authenticator, 1)[0]
        action = self._action(
            self._flow(),
            action_type="user.update",
            target_uuid=user.uuid,
            values={"real_name": "Changed name", "password": "n3w-p4ss"},
            base_values={"real_name": user.real_name},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types.authenticators.user.RestProxy._execute_sync") as execute_sync:
            execute_sync.return_value = "done"
            summary = async_to_sync(UserUpdate().execute)(action, request=make_request())

        self.assertIn("updated", summary)
        target = execute_sync.call_args[0][0]
        self.assertIs(target.handler, Users)
        self.assertEqual(target.path, "authenticators/{uuid}/users")
        params: dict[str, typing.Any] = execute_sync.call_args[0][2]
        # Proposed values win, required columns keep stored values,
        # write-only password travels only because it was proposed
        self.assertEqual(params["real_name"], "Changed name")
        self.assertEqual(params["password"], "n3w-p4ss")
        self.assertEqual(params["name"], user.name)
        self.assertEqual(params["comments"], user.comments)
        self.assertEqual(params["state"], user.state)
        # Privileges travel as immutable context (the PUT requires them)
        self.assertIs(params["staff_member"], user.staff_member)
        self.assertIs(params["is_admin"], user.is_admin)
        # The membership rides the PUT (current direct groups)
        self.assertEqual(params["groups"], list(user.groups.values_list("uuid", flat=True)))
        # Parent authenticator derived from the manager FK
        self.assertEqual(execute_sync.call_args[0][3], authenticator.uuid)

    def test_membership_update_normalizes_csv_proposals(self) -> None:
        authenticator = create_db_authenticator()
        groups = create_db_groups(authenticator, 2)
        user = create_db_users(authenticator, 1)[0]
        action = self._action(
            self._flow(),
            action_type="user.update",
            target_uuid=user.uuid,
            values={"groups": f"{groups[0].uuid},{groups[1].uuid}"},
            base_values={"comments": user.comments},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types.authenticators.user.RestProxy._execute_sync") as execute_sync:
            execute_sync.return_value = "done"
            async_to_sync(UserUpdate().execute)(action, request=make_request())

        params: dict[str, typing.Any] = execute_sync.call_args[0][2]
        self.assertEqual(params["groups"], [groups[0].uuid, groups[1].uuid])


class ExternalAuthenticatorUserTest(FlowTestCase):
    """External authenticators sync their users (and groups) from the
    provider: membership is neither proposable nor part of the CAS."""

    def _external_user(self) -> typing.Any:
        from types import SimpleNamespace

        authenticator = create_db_authenticator()
        user = create_db_users(authenticator, 1, groups=create_db_groups(authenticator, 1))[0]
        patcher = mock.patch.object(
            models.Authenticator,
            "get_instance",
            return_value=SimpleNamespace(external_source=True),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return user

    def test_membership_is_not_proposable_on_external_authenticators(self) -> None:
        user = self._external_user()
        action_type = UserUpdate()

        by_name = {d["name"] for d in action_type.field_definitions("user", user)}
        self.assertNotIn("groups", by_name)

        errors = action_type.validate_values("user", {"groups": ["some-uuid"]}, target=user)
        self.assertTrue(any("unknown fields" in e for e in errors))

    def test_membership_is_outside_the_cas_on_external_authenticators(self) -> None:
        user = self._external_user()
        action_type = UserUpdate()

        self.assertNotIn("groups", action_type.etag_fields("user", user))
        self.assertNotIn("groups", action_type.snapshot_values(user, action_type.etag_fields("user", user)))

    def test_membership_is_purged_from_the_execution_payload(self) -> None:
        user = self._external_user()
        action = self._action(
            self._flow(),
            action_type="user.update",
            target_uuid=user.uuid,
            values={"groups": ["some-uuid"]},
            base_values={"comments": user.comments},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types.authenticators.user.RestProxy._execute_sync") as execute_sync:
            execute_sync.return_value = "done"
            async_to_sync(UserUpdate().execute)(action, request=make_request())

        params: dict[str, typing.Any] = execute_sync.call_args[0][2]
        self.assertNotIn("groups", params)


class UserCreateDeleteTest(FlowTestCase):
    """user.create / user.delete: detail creation and removal."""

    def test_supported_operations_derive_from_hooks(self) -> None:
        self.assertEqual(
            UserUpdate.supported_operations(),
            frozenset(
                {
                    ActionOperation.UPDATE,
                    ActionOperation.CREATE,
                    ActionOperation.DELETE,
                    ActionOperation.CUSTOM,
                }
            ),
        )

    def test_creation_targets_the_parent_authenticator(self) -> None:
        self.assertTrue(UserUpdate.create_needs_parent)
        self.assertEqual(UserUpdate(ActionOperation.CREATE).full_id, "user.create")
        delete = UserUpdate(ActionOperation.DELETE)
        self.assertEqual(delete.full_id, "user.delete")
        self.assertEqual(delete.get_stale_policy(), StalePolicy.DENY)

    def test_create_execution_posts_the_detail_with_safe_defaults(self) -> None:
        authenticator = create_db_authenticator()
        create = UserUpdate(ActionOperation.CREATE)
        action = build_action(
            action_type="user.create",
            target_uuid=authenticator.uuid,
            values={"name": "new-user"},
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy._execute_sync") as execute_sync:
            execute_sync.return_value = {"id": "usr-uuid"}
            summary = async_to_sync(create.execute)(action, request=make_request())
            target, _request, params, parent_uuid = execute_sync.call_args[0]
            self.assertEqual(
                (target.handler, target.method.value, target.args),
                (Users, "POST", ()),
            )
            self.assertEqual(parent_uuid, authenticator.uuid)
            # The POST requires the basic columns: safe defaults fill the
            # omitted ones...
            self.assertEqual(params["name"], "new-user")
            self.assertEqual(params["real_name"], "")
            self.assertEqual(params["state"], "A")
            # ...and privileges NEVER come from a proposal
            self.assertFalse(params["staff_member"])
            self.assertFalse(params["is_admin"])
            # Write-only fields travel only when proposed
            self.assertNotIn("password", params)
            self.assertNotIn("mfa_data", params)
        self.assertIn("new-user", summary)
        self.assertIn("usr-uuid", summary)

    def test_create_execution_sends_password_only_when_proposed(self) -> None:
        authenticator = create_db_authenticator()
        create = UserUpdate(ActionOperation.CREATE)
        action = build_action(
            action_type="user.create",
            target_uuid=authenticator.uuid,
            values={"name": "with-pass", "password": "s3cr3t", "mfa_data": "  mn  "},
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy._execute_sync") as execute_sync:
            execute_sync.return_value = {"id": "usr-uuid"}
            async_to_sync(create.execute)(action, request=make_request())
            params = execute_sync.call_args[0][2]
            self.assertEqual(params["password"], "s3cr3t")
            self.assertEqual(params["mfa_data"], "mn")

    def test_delete_execution_removes_with_parent_derived(self) -> None:
        authenticator = create_db_authenticator()
        user = create_db_users(authenticator, 1)[0]
        delete = UserUpdate(ActionOperation.DELETE)
        action = build_action(
            action_type="user.delete",
            target_uuid=user.uuid,
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
                (Users, "DELETE", (user.uuid,)),
            )
            self.assertEqual(params, {})
            self.assertEqual(parent_uuid, authenticator.uuid)
        self.assertIn(user.name, summary)
        self.assertIn("deleted", summary)


class UserCustomTest(FlowTestCase):
    """user.custom: the entity-scoped verbs of the user family."""

    def _custom(self) -> UserUpdate:
        return UserUpdate(ActionOperation.CUSTOM)

    def _user_with_mfa(self) -> typing.Any:
        authenticator = create_db_authenticator()
        authenticator.mfa = create_db_mfa()
        authenticator.save(update_fields=["mfa_id"])
        return create_db_users(authenticator, 1)[0]

    def test_is_registered_and_forced(self) -> None:
        self.assertIn("user.custom", all_type_ids())
        self.assertEqual(self._custom().full_id, "user.custom")
        # An idempotent verb: unrelated drift must not revoke the proposal
        self.assertIs(self._custom().get_stale_policy(), StalePolicy.FORCE)

    def test_publishes_only_the_action_choice(self) -> None:
        defs = self._custom().field_definitions("user")
        self.assertEqual([d["name"] for d in defs], ["action"])
        self.assertEqual(defs[0]["type"], types.ui.FieldType.CHOICE.value)
        self.assertEqual(defs[0]["choices"], ["clean_related"])

    def test_choices_follow_the_mfa_binding(self) -> None:
        user = create_db_users(create_db_authenticator(), 1)[0]
        defs = self._custom().field_definitions("user", user)
        self.assertEqual(defs[0]["choices"], [])
        self.assertIn("supports none of these actions", defs[0]["tooltip"])
        mfa_user = self._user_with_mfa()
        defs = self._custom().field_definitions("user", mfa_user)
        self.assertEqual(defs[0]["choices"], ["clean_related"])
        self.assertIn("supports: clean_related", defs[0]["tooltip"])

    def test_action_field_is_required(self) -> None:
        user = self._user_with_mfa()
        errors = self._custom().validate_values("user", {"something": "x"}, user)
        self.assertTrue(any("field action is required" in e for e in errors))

    def test_unknown_verb_rejected(self) -> None:
        user = self._user_with_mfa()
        errors = self._custom().validate_values("user", {"action": "detach_services"}, user)
        self.assertTrue(any("clean_related" in e for e in errors))

    def test_no_mfa_verb_rejected(self) -> None:
        user = create_db_users(create_db_authenticator(), 1)[0]
        errors = self._custom().validate_values("user", {"action": "clean_related"}, user)
        self.assertTrue(any("no MFA bound" in e for e in errors))

    def test_valid_verb_passes(self) -> None:
        user = self._user_with_mfa()
        self.assertEqual(self._custom().validate_values("user", {"action": "clean_related"}, user), [])

    def test_execution_posts_the_named_custom_method(self) -> None:
        user = self._user_with_mfa()
        action = build_action(
            action_type="user.custom",
            target_uuid=user.uuid,
            values={"action": "clean_related"},
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mcp.rest_proxy.RestProxy._execute_sync") as execute_sync:
            execute_sync.return_value = {"status": "ok"}
            summary = async_to_sync(self._custom().execute)(action, request=make_request())
            target, _request, params, parent_uuid = execute_sync.call_args[0]
            self.assertEqual(
                (target.handler, target.method.value, target.args),
                (Users, "POST", (user.uuid, "clean_related")),
            )
            self.assertEqual(params, {})
            self.assertEqual(parent_uuid, user.manager.uuid)
        self.assertIn(user.name, summary)
        self.assertIn("related data cleaned", summary)

    def test_execution_revalidates_the_mfa_gate(self) -> None:
        user = self._user_with_mfa()
        action = build_action(
            action_type="user.custom",
            target_uuid=user.uuid,
            values={"action": "clean_related"},
            base_values={},
            base_etag="",
        )
        # FORCE keeps the proposal alive through drift: if the MFA was
        # unbound after proposing, the execution refuses on the live row
        user.manager.mfa = None
        user.manager.save(update_fields=["mfa_id"])
        with mock.patch("uds.mcp.rest_proxy.RestProxy._execute_sync") as execute_sync:
            with self.assertRaises(rest_exceptions.NotSupportedError):
                async_to_sync(self._custom().execute)(action, request=make_request())
            execute_sync.assert_not_called()

    def test_tool_text_names_the_verb(self) -> None:
        custom = self._custom()
        self.assertEqual(custom.tool_title(), "Propose an action on a user")
        description = custom.tool_description()
        self.assertIn("clean_related", description)
        self.assertIn("MFA", description)
        self.assertIn("queued until an administrator approves it", description)
