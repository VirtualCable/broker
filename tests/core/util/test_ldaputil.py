#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
"""
Author: dkmaster
Author: Janier Rodríguez, jrodriguez at virtualcable dot es

Unit tests for ``uds.core.util.ldaputil`` (native backend).

The native client is exercised through ``MagicMock(spec=NativeConnection)``
fakes, so the ``isinstance`` dispatch inside ldaputil routes the calls to
the mock without needing a live server. The semantics asserted here are the
ones the UDS modules depend on:

* ``as_dict`` attribute fill parity (requested-but-missing -> [""]).
* ``LDAPReferralError`` semantics and ``raise_on_referrals`` plumbed
  through ``as_dict`` / ``first``.
* ``connection()`` surfacing the server diagnostic message on bind failure
  (so callers like the AD authenticator can detect sub-status codes such
  as "773" password must be reset), and the BindError / LDAPError split.
* add / modify mapping to AlreadyExistsError, and recursive delete
  ordering (deepest first).
"""

import typing
import unittest
from unittest import mock

from django.conf import settings

from uds.core.util import ldaputil

from ...utils.test import UDSTestCase


def _native_mock() -> mock.MagicMock:
    """A mock that passes the ``isinstance(con, NativeConnection)`` check."""
    return mock.MagicMock(spec=ldaputil.NativeConnection)


class LDAPReferralErrorTest(UDSTestCase):
    def test_referrals_attribute_carries_uris(self) -> None:
        err: ldaputil.LDAPReferralError = ldaputil.LDAPReferralError(
            ["ldap://dc1.corp.local/dc=corp,dc=local", "ldap://dc2.corp.local/dc=corp,dc=local"]
        )
        self.assertEqual(
            err.referrals,
            ["ldap://dc1.corp.local/dc=corp,dc=local", "ldap://dc2.corp.local/dc=corp,dc=local"],
        )
        # LDAPReferralError must remain an LDAPError so callers catching
        # the broader type keep working.
        self.assertIsInstance(err, ldaputil.LDAPError)

    def test_drops_empty_entries(self) -> None:
        err: ldaputil.LDAPReferralError = ldaputil.LDAPReferralError(["", "ldap://a/", ""])
        self.assertEqual(err.referrals, ["ldap://a/"])

    def test_str_includes_referrals(self) -> None:
        err: ldaputil.LDAPReferralError = ldaputil.LDAPReferralError(["ldap://a/"])
        self.assertIn("ldap://a/", str(err))

    def test_carries_partial_results(self) -> None:
        partial: ldaputil.LDAPResultType = {"dn": "cn=alice,dc=corp,dc=local"}
        err: ldaputil.LDAPReferralError = ldaputil.LDAPReferralError(
            ["ldap://child.corp.local/dc=child"],
            partial_results=[partial],
        )
        self.assertEqual(err.partial_results, [partial])


class AsDictNativeTest(UDSTestCase):
    def _search_response(
        self,
        entries: list[dict[str, typing.Any]],
        referrals: list[str] | None = None,
    ) -> dict[str, typing.Any]:
        return {"entries": entries, "referrals": referrals or []}

    def test_yields_all_attributes_when_none_requested(self) -> None:
        con = _native_mock()
        con.search.return_value = self._search_response(
            [
                {
                    "dn": "uid=alice,dc=corp,dc=local",
                    "uid": ["alice"],
                    "cn": ["Alice"],
                }
            ]
        )
        rows = list(ldaputil.as_dict(con, "dc=corp,dc=local", "(uid=alice)"))  # type: ignore[arg-type]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["uid"], ["alice"])
        self.assertEqual(rows[0]["cn"], ["Alice"])
        self.assertEqual(rows[0]["dn"], "uid=alice,dc=corp,dc=local")
        # All-attributes search: attributes=None reaches the native client.
        con.search.assert_called_once()
        self.assertIsNone(con.search.call_args.kwargs["attributes"])

    def test_fills_missing_requested_attributes_with_empty_string(self) -> None:
        con = _native_mock()
        con.search.return_value = self._search_response(
            [{"dn": "uid=alice,dc=corp,dc=local", "uid": ["alice"]}]
        )
        rows = list(
            ldaputil.as_dict(
                con,  # type: ignore[arg-type]
                "dc=corp,dc=local",
                "(uid=alice)",
                attributes=["uid", "mail", "nonexistent"],
            )
        )
        self.assertEqual(rows[0]["uid"], ["alice"])
        self.assertEqual(rows[0]["mail"], [""])
        self.assertEqual(rows[0]["nonexistent"], [""])
        self.assertEqual(rows[0]["dn"], "uid=alice,dc=corp,dc=local")

    def test_search_error_maps_to_ldap_error(self) -> None:
        con = _native_mock()
        con.search.side_effect = ldaputil.NativeLdapError("boom")
        with self.assertRaises(ldaputil.LDAPError):
            list(ldaputil.as_dict(con, "dc=corp,dc=local", "(uid=alice)"))  # type: ignore[arg-type]

    def test_default_drops_referrals_silently(self) -> None:
        """``raise_on_referrals`` defaults to False for backwards compat."""
        con = _native_mock()
        con.search.return_value = self._search_response([], referrals=["ldap://other-dc/dc=corp,dc=local"])
        rows: list[ldaputil.LDAPResultType] = list(
            ldaputil.as_dict(con, "dc=corp,dc=local", "(uid=alice)")  # type: ignore[arg-type]
        )
        self.assertEqual(rows, [])

    def test_raise_on_referrals_surfaces_uris(self) -> None:
        con = _native_mock()
        con.search.return_value = self._search_response(
            [],
            referrals=["ldap://other-dc/dc=corp,dc=local", "ldap://other-dc2/dc=corp,dc=local"],
        )
        with self.assertRaises(ldaputil.LDAPReferralError) as ctx:
            list(
                ldaputil.as_dict(
                    con,  # type: ignore[arg-type]
                    "dc=corp,dc=local",
                    "(uid=alice)",
                    raise_on_referrals=True,
                )
            )
        self.assertEqual(
            ctx.exception.referrals,
            ["ldap://other-dc/dc=corp,dc=local", "ldap://other-dc2/dc=corp,dc=local"],
        )

    def test_raise_on_referrals_yields_entries_when_none_returned(self) -> None:
        con = _native_mock()
        con.search.return_value = self._search_response(
            [{"dn": "cn=alice,dc=corp,dc=local", "uid": ["alice"], "cn": ["Alice"]}]
        )
        rows = list(
            ldaputil.as_dict(
                con,  # type: ignore[arg-type]
                "dc=corp,dc=local",
                "(uid=alice)",
                attributes=["uid", "cn"],
                raise_on_referrals=True,
            )
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["uid"], ["alice"])
        self.assertEqual(rows[0]["dn"], "cn=alice,dc=corp,dc=local")

    def test_raise_on_referrals_preserves_partial_entries(self) -> None:
        con = _native_mock()
        con.search.return_value = self._search_response(
            [{"dn": "cn=alice,dc=corp,dc=local", "uid": ["alice"]}],
            referrals=["ldap://child-dc/dc=child,dc=corp,dc=local"],
        )
        with self.assertRaises(ldaputil.LDAPReferralError) as ctx:
            list(
                ldaputil.as_dict(
                    con,  # type: ignore[arg-type]
                    "dc=corp,dc=local",
                    "(uid=*)",
                    attributes=["uid"],
                    raise_on_referrals=True,
                )
            )
        self.assertEqual(ctx.exception.partial_results[0]["uid"], ["alice"])

    def test_scopes_are_forwarded(self) -> None:
        con = _native_mock()
        con.search.return_value = self._search_response([])
        list(ldaputil.as_dict(con, "dc=corp,dc=local", "(objectClass=*)", scope=ldaputil.SCOPE_BASE))  # type: ignore[arg-type]
        self.assertEqual(con.search.call_args.kwargs["scope"], ldaputil.SCOPE_BASE)


class FirstNativeTest(UDSTestCase):
    def _con_with_entry(self, entry: dict[str, typing.Any] | None) -> mock.MagicMock:
        con = _native_mock()
        con.search.return_value = {"entries": [entry] if entry else [], "referrals": []}
        return con

    def test_first_found(self) -> None:
        con = self._con_with_entry({"dn": "uid=alice,dc=corp,dc=local", "uid": ["alice"], "cn": ["Alice"]})
        obj = ldaputil.first(con, "dc=corp,dc=local", "inetOrgPerson", "uid", "alice", attributes=["cn"])  # type: ignore[arg-type]
        assert obj is not None
        self.assertEqual(obj["cn"], ["Alice"])
        self.assertEqual(obj["_id"], "alice")

    def test_first_not_found(self) -> None:
        con = self._con_with_entry(None)
        self.assertIsNone(
            ldaputil.first(con, "dc=corp,dc=local", "inetOrgPerson", "uid", "nobody")  # type: ignore[arg-type]
        )

    def test_first_escapes_value(self) -> None:
        # Wildcards in the value must be escaped, so the filter never
        # matches other users.
        con = self._con_with_entry(None)
        ldaputil.first(con, "dc=corp,dc=local", "inetOrgPerson", "uid", "al*")  # type: ignore[arg-type]
        filter_used = con.search.call_args.args[1]
        self.assertIn("(uid=al\\2a)", filter_used)


class AddModifyNativeTest(UDSTestCase):
    def test_add_ok(self) -> None:
        con = _native_mock()
        self.assertTrue(
            ldaputil.add(con, "cn=x,dc=x", attributes={"cn": ["x"]})  # type: ignore[arg-type]
        )
        con.add.assert_called_once_with("cn=x,dc=x", attributes={"cn": ["x"]})

    def test_add_raises_already_exists(self) -> None:
        con = _native_mock()
        con.add.side_effect = ldaputil.NativeAlreadyExistsError("already exists")
        with self.assertRaises(ldaputil.ALREADY_EXISTS):
            ldaputil.add(con, "cn=x,dc=x", attributes={"cn": ["x"]})  # type: ignore[arg-type]

    def test_add_raises_ldap_error_for_other_failures(self) -> None:
        con = _native_mock()
        con.add.side_effect = ldaputil.NativeLdapError("insufficient access")
        with self.assertRaises(ldaputil.LDAPError) as ctx:
            ldaputil.add(con, "cn=x,dc=x", attributes={"cn": ["x"]})  # type: ignore[arg-type]
        self.assertNotIsInstance(ctx.exception, ldaputil.AlreadyExistsError)

    def test_modify_ok(self) -> None:
        con = _native_mock()
        self.assertTrue(
            ldaputil.modify(
                con,  # type: ignore[arg-type]
                "cn=group,dc=x",
                {"member": [(ldaputil.MODIFY_ADD, ["cn=machine,dc=x"])]},
            )
        )
        con.modify.assert_called_once_with(
            "cn=group,dc=x",
            changes={"member": [(ldaputil.MODIFY_ADD, ["cn=machine,dc=x"])]},
        )

    def test_modify_raises_already_exists(self) -> None:
        con = _native_mock()
        con.modify.side_effect = ldaputil.NativeAlreadyExistsError("value exists")
        with self.assertRaises(ldaputil.ALREADY_EXISTS):
            ldaputil.modify(
                con,  # type: ignore[arg-type]
                "cn=group,dc=x",
                {"member": [(ldaputil.MODIFY_ADD, ["cn=machine,dc=x"])]},
            )

    def test_modify_rejects_controls(self) -> None:
        con = _native_mock()
        with self.assertRaises(ldaputil.LDAPError):
            ldaputil.modify(
                con,  # type: ignore[arg-type]
                "cn=group,dc=x",
                {"member": [(ldaputil.MODIFY_ADD, ["cn=x"])]},
                controls=mock.MagicMock(),
            )
        con.modify.assert_not_called()


class RootDseNativeTest(UDSTestCase):
    def test_returns_the_attributes_of_the_root_entry(self) -> None:
        con = _native_mock()
        con.search.return_value = {
            "entries": [
                {
                    "dn": "",
                    "namingContexts": ["dc=corp,dc=local", "cn=Configuration,dc=corp,dc=local"],
                    "supportedLDAPVersion": ["3"],
                }
            ],
            "referrals": [],
        }
        result = ldaputil.get_root_dse(con)  # type: ignore[arg-type]
        assert result is not None
        self.assertEqual(result["namingContexts"], ["dc=corp,dc=local", "cn=Configuration,dc=corp,dc=local"])
        self.assertEqual(result["supportedLDAPVersion"], ["3"])
        self.assertEqual(result["dn"], "")
        # Root DSE: base scope over "" with operational attributes
        self.assertEqual(con.search.call_args.args[0], "")
        self.assertEqual(con.search.call_args.kwargs["scope"], ldaputil.SCOPE_BASE)
        self.assertEqual(con.search.call_args.kwargs["attributes"], ["*", "+"])

    def test_returns_none_when_the_server_answers_nothing(self) -> None:
        con = _native_mock()
        con.search.return_value = {"entries": [], "referrals": []}
        self.assertIsNone(ldaputil.get_root_dse(con))  # type: ignore[arg-type]

    def test_maps_search_errors(self) -> None:
        con = _native_mock()
        con.search.side_effect = ldaputil.NativeLdapError("boom")
        with self.assertRaises(ldaputil.LDAPError):
            ldaputil.get_root_dse(con)  # type: ignore[arg-type]


class RecursiveDeleteNativeTest(UDSTestCase):
    """windows_domain pattern: delete an entry tree, deepest first."""

    ENTRY_DN = "cn=udspqv001,ou=vdi,dc=example,dc=local"
    CHILD_DN = f"cn=child,{ENTRY_DN}"
    GRANDCHILD_DN = f"cn=grandchild,{CHILD_DN}"

    def _con_with_tree(self, deleted: list[str]) -> mock.MagicMock:
        con = _native_mock()
        tree: dict[str, list[str]] = {
            self.ENTRY_DN: [self.CHILD_DN],
            self.CHILD_DN: [self.GRANDCHILD_DN],
            self.GRANDCHILD_DN: [],
        }

        def search(base: str, *args: typing.Any, **kwargs: typing.Any) -> dict[str, typing.Any]:
            return {
                "entries": [{"dn": child} for child in tree.get(base, [])],
                "referrals": [],
            }

        def delete(dn: str, *args: typing.Any, **kwargs: typing.Any) -> None:
            deleted.append(dn)

        con.search.side_effect = search
        con.delete.side_effect = delete
        return con

    def test_recursive_delete_removes_the_subtree_deepest_first(self) -> None:
        deleted: list[str] = []
        con = self._con_with_tree(deleted)
        ldaputil.recursive_delete(con, self.ENTRY_DN)  # type: ignore[arg-type]
        self.assertEqual(deleted, [self.GRANDCHILD_DN, self.CHILD_DN, self.ENTRY_DN])

    def test_recursive_delete_of_a_leaf_only_deletes_the_entry(self) -> None:
        deleted: list[str] = []
        con = _native_mock()
        con.search.side_effect = lambda *a, **k: {"entries": [], "referrals": []}
        con.delete.side_effect = deleted.append
        ldaputil.recursive_delete(con, self.ENTRY_DN)  # type: ignore[arg-type]
        self.assertEqual(deleted, [self.ENTRY_DN])


class ConnectionNativeTest(UDSTestCase):
    """``connection()`` wiring and error mapping on the native backend.

    The AD authenticator's ``check_password`` keys off substrings such as
    ``AcceptSecurityContext error, data 773`` to redirect users to a
    password changer; the native BindError message must carry the server
    diagnostic text for that to keep working.
    """

    def _connect(
        self,
        side_effect: typing.Any = None,
        return_value: typing.Any = None,
        **kwargs: typing.Any,
    ) -> typing.Any:
        factory = mock.patch.object(
            ldaputil,
            "NativeConnection",
            return_value=return_value,
            side_effect=side_effect,
        )
        return factory

    def test_parameters_are_forwarded(self) -> None:
        fake = _native_mock()
        with (
            mock.patch.object(ldaputil, "NativeConnection", return_value=fake) as native,
            mock.patch.object(settings, "SECURE_MIN_TLS_VERSION", "1.3", create=True),
            mock.patch.object(settings, "SECURE_CIPHERS", "TLS_AES_128_GCM_SHA256", create=True),
        ):
            con: ldaputil.LDAPConnection = ldaputil.connection(
                username="alice@corp.local",
                passwd="ok",
                host="dc.corp.local",
                use_ssl=True,
                verify_ssl=True,
                certificate_data="",
                timeout=5,
            )
        self.assertIs(con, fake)
        args, kwargs = native.call_args
        # Default port for use_ssl is 636
        self.assertEqual(args, ("dc.corp.local", 636, "alice@corp.local", "ok"))
        self.assertTrue(kwargs["use_ssl"])
        self.assertTrue(kwargs["verify_ssl"])
        # Empty certificate content must behave as "no custom CA".
        self.assertIsNone(kwargs["ca_certs_data"])
        self.assertEqual(kwargs["min_tls_version"], "1.3")
        self.assertEqual(kwargs["ciphers"], "TLS_AES_128_GCM_SHA256")
        self.assertEqual(kwargs["timeout"], 5.0)

    def test_default_ports(self) -> None:
        for use_ssl, expected_port in ((False, 389), (True, 636)):
            with self.subTest(use_ssl=use_ssl):
                fake = _native_mock()
                with mock.patch.object(ldaputil, "NativeConnection", return_value=fake) as native:
                    ldaputil.connection("u", "p", "dc.corp.local", use_ssl=use_ssl)
                self.assertEqual(native.call_args.args[1], expected_port)

    def test_failed_bind_raises_bind_error_with_message(self) -> None:
        error = ldaputil.NativeBindError(
            "Could not bind to LDAP server dc.corp.local:389 as alice@corp.local: "
            "80090308: LdapErr: DSID-0C0903A9, comment: AcceptSecurityContext error, data 773, v3830"
        )
        with (
            self._connect(side_effect=error),
            self.assertRaises(ldaputil.BindError) as ctx,
        ):
            ldaputil.connection(
                username="alice@corp.local",
                passwd="bad",
                host="dc.corp.local",
                port=389,
                use_ssl=False,
                timeout=5,
            )
        self.assertIn("AcceptSecurityContext error, data 773", str(ctx.exception))
        self.assertIn("dc.corp.local", str(ctx.exception))

    def test_connect_failure_raises_plain_ldap_error(self) -> None:
        error = ldaputil.NativeLdapError("I/O error: Connection refused (os error 111)")
        with (
            self._connect(side_effect=error),
            self.assertRaises(ldaputil.LDAPError) as ctx,
        ):
            ldaputil.connection("u", "p", "dc.corp.local", port=636, use_ssl=True, timeout=2)
        self.assertNotIsInstance(ctx.exception, ldaputil.BindError)
        self.assertIn("Connection refused", str(ctx.exception))

    def test_successful_bind_returns_connection(self) -> None:
        fake = _native_mock()
        with self._connect(return_value=fake):
            con: ldaputil.LDAPConnection = ldaputil.connection(
                username="alice@corp.local",
                passwd="ok",
                host="dc.corp.local",
                port=389,
                use_ssl=False,
                timeout=5,
            )
        self.assertIs(con, fake)


if __name__ == "__main__":
    unittest.main()
