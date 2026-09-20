"""``account`` (create / update / delete): registration, discovery, CAS and execution shape."""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.consts.mcp import CREATE_TARGET_UUID
from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.base import ActionOperation
from uds.mutability.types.accounts import AccountUpdate
from uds.models import Account
from uds.REST.methods.accounts import Accounts

from tests.mcp.mutability._helpers import FlowTestCase, build_action, make_request

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


def _bound(operation: str) -> AccountUpdate:
    factory = registry_get(f"account.{operation}")
    assert factory is not None
    return typing.cast(AccountUpdate, factory())


class AccountUpdateTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("account.update")
        assert found is not None
        self.assertIs(type(found()), AccountUpdate)
        self.assertIn("account.update", all_type_ids())
        self.assertIs(type(_bound("create")), AccountUpdate)
        self.assertIs(type(_bound("delete")), AccountUpdate)
        self.assertEqual(
            AccountUpdate.supported_operations(),
            frozenset({ActionOperation.UPDATE, ActionOperation.CREATE, ActionOperation.DELETE}),
        )

    def test_fields_and_cas(self) -> None:
        account = Account.objects.create(name="acc1")
        action_type = AccountUpdate()

        defs = action_type.field_definitions("account")
        names = [d["name"] for d in defs]
        self.assertEqual(sorted(names), ["comments", "name", "tags"])
        # Types come straight from the handler gui (tags is a taglist, not
        # the text the old hardcoded defs lied about)
        by_name = {d["name"]: d for d in defs}
        self.assertEqual(by_name["tags"]["type"], "taglist")
        self.assertEqual(by_name["name"]["type"], "text")
        self.assertTrue(all(d["from_instance"] is False for d in defs))

        snapshot = action_type.snapshot_values(account, ["name", "tags"])
        self.assertEqual(snapshot, {"name": "acc1", "tags": []})

        base = action_type.fingerprint(account)
        account.comments = "changed"
        account.save(update_fields=["comments"])
        self.assertNotEqual(base, action_type.fingerprint(account))

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            AccountUpdate().resolve_target("00000000-0000-0000-0000-000000000000")

    def test_execute_sends_rest_put_shape(self) -> None:
        account = Account.objects.create(name="acc1")
        action = self._action(
            self._flow(),
            action_type="account.update",
            target_uuid=account.uuid,
            values={"comments": "new"},
            base_values={"comments": ""},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types.accounts.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            # async_to_sync mirrors the production executor: the
            # thread-sensitive ORM work runs on the caller thread
            summary = async_to_sync(AccountUpdate().execute)(action, request=make_request())

        self.assertIn("updated", summary)
        execute_call = proxy_cls.return_value.execute.call_args
        self.assertIs(execute_call[0][0].handler, Accounts)
        # The PUT is form-shaped: the proposal travels merged over the
        # current values, never alone
        self.assertEqual(execute_call[0][2], {"name": "acc1", "comments": "new", "tags": []})


class AccountCreateTest(FlowTestCase):
    """account.create: the surface, validation, POST shape."""

    def test_creation_view_offers_the_save_fields(self) -> None:
        names = [d["name"] for d in _bound("create").create_field_definitions("account")]
        self.assertEqual(sorted(names), ["comments", "name", "tags"])

    def test_create_requires_name(self) -> None:
        create = _bound("create")
        self.assertTrue(any("name" in e for e in create.create_validate_values("account", {})))
        self.assertTrue(any("name" in e for e in create.create_validate_values("account", {"name": "   "})))
        self.assertEqual(create.create_validate_values("account", {"name": "acc"}), [])

    def test_create_execution_posts_the_form_shape(self) -> None:
        action = build_action(
            action_type="account.create",
            target_uuid=CREATE_TARGET_UUID,
            values={"name": "new acc"},
            base_values={},
            base_etag="",
        )
        with mock.patch("uds.mutability.verbs.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value={"id": "acc-uuid"})
            summary = async_to_sync(_bound("create").execute)(action, request=make_request())
            target, _request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual(
                (target.handler, target.path, target.method.value, target.args),
                (Accounts, "accounts", "POST", ()),
            )
        # omitted form fields ride the admin "new" defaults
        self.assertEqual(params, {"name": "new acc", "comments": "", "tags": []})
        self.assertIn("created", summary)
        self.assertIn("acc-uuid", summary)


class AccountDeleteTest(FlowTestCase):
    def test_delete_publishes_no_fields(self) -> None:
        delete = _bound("delete")
        self.assertEqual(delete.field_definitions("account"), [])
        # an empty payload is the normal shape of a deletion proposal
        self.assertEqual(delete.validate_values("account", {}, None), [])
        self.assertIn("Propose deleting an account", delete.tool_title())
        self.assertIn("usage", delete.tool_description())

    def test_delete_executes_the_rest_call(self) -> None:
        account = Account.objects.create(name="acc1")
        action = self._action(
            self._flow(),
            action_type="account.delete",
            target_uuid=account.uuid,
            values={},
            base_values={},
            base_etag="etag",
        )
        with mock.patch("uds.mutability.verbs.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            summary = async_to_sync(_bound("delete").execute)(action, request=make_request())
            target, _request, params = proxy_cls.return_value.execute.call_args[0]
            self.assertEqual(
                (target.handler, target.path, target.method.value, target.args),
                (Accounts, "accounts", "DELETE", (account.uuid,)),
            )
            self.assertEqual(params, {})
        self.assertIn("deleted", summary)
        self.assertIn("acc1", summary)

    def test_delete_missing_target_is_not_found(self) -> None:
        action = self._action(
            self._flow(),
            action_type="account.delete",
            target_uuid="00000000-0000-0000-0000-000000000000",
            values={},
            base_values={},
            base_etag="etag",
        )
        with self.assertRaises(rest_exceptions.NotFound):
            async_to_sync(_bound("delete").execute)(action, request=make_request())
