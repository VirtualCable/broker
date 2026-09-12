"""``account.update``: registration, discovery, CAS and execution shape."""

from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_types, get as registry_get
from uds.mutability.types.accounts import AccountUpdate
from uds.models import Account
from uds.REST.methods.accounts import Accounts

from tests.mcp.mutability._helpers import FlowTestCase, make_request

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


class AccountUpdateTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("account.update")
        self.assertIs(found, AccountUpdate)
        self.assertIn("account.update", [t.type_id for t in all_types()])

    def test_fields_and_cas(self) -> None:
        account = Account.objects.create(name="acc1")
        action_type = AccountUpdate()

        names = [d["name"] for d in action_type.field_definitions("account")]
        self.assertEqual(sorted(names), ["comments", "name", "tags"])

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
        self.assertEqual(execute_call[0][2], {"comments": "new"})
