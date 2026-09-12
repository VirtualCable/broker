"""``user.update``: registration, discovery, CAS and execution shape."""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_types, get as registry_get
from uds.mutability.types.users import UserUpdate
from uds.REST.methods.users_groups import Users

from tests.fixtures.authenticators import create_db_authenticator, create_db_users
from tests.mcp.mutability._helpers import FlowTestCase, make_request


class UserUpdateRegistryTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("user.update")
        self.assertIs(found, UserUpdate)
        self.assertIn("user.update", [t.type_id for t in all_types()])

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            UserUpdate().resolve_target("00000000-0000-0000-0000-000000000000")


class UserUpdateFieldsTest(FlowTestCase):
    def test_field_definitions_and_write_only_password(self) -> None:
        defs = UserUpdate().field_definitions("user")
        by_name = {d["name"]: d for d in defs}

        for name in ("name", "real_name", "comments", "state", "mfa_data", "password"):
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

        with mock.patch("uds.mutability.types.users.RestProxy._execute_sync") as execute_sync:
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
        # Parent authenticator derived from the manager FK
        self.assertEqual(execute_sync.call_args[0][3], authenticator.uuid)
