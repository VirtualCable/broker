"""``account.update``: registration, discovery, CAS and execution shape."""

from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.types.accounts import AccountUpdate
from uds.models import Account
from uds.REST.methods.accounts import Accounts

from tests.mcp.mutability._helpers import FlowTestCase, make_request

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


class AccountUpdateTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("account.update")
        assert found is not None
        self.assertIs(type(found()), AccountUpdate)
        self.assertIn("account.update", all_type_ids())

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
