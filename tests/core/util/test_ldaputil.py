# -*- coding: utf-8 -*-
#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
"""
Author: dkmaster

Unit tests for ``uds.core.util.ldaputil`` focused on the bits that aren't
covered by ``test_ldaputil_helpers.py``:

* :class:`ldaputil.LDAPReferralError` semantics and ``raise_on_referrals``
  plumbed through ``as_dict`` / ``first``.
* :func:`ldaputil.connection` preserving the underlying LDAP ``result``
  ``message`` when the bind fails (so callers like the AD authenticator
  can detect sub-status codes such as ``773`` "password must be reset").

The existing :class:`FakeLDAPConnection` covers ``add``/``modify`` error
mapping and lives alongside these tests so the surface stays small.
"""

import typing
import unittest
from unittest import mock

from uds.core.util import ldaputil

from ...utils.test import UDSTestCase


class FakeLDAPConnection:
    def __init__(self, result: dict[str, object], *, add_result: bool = True, modify_result: bool = True):
        self.result = result
        self._add_result = add_result
        self._modify_result = modify_result

    def add(self, dn: str, attributes: dict[str, list[bytes | str]]) -> bool:
        del dn, attributes
        return self._add_result

    def modify(
        self,
        dn: str,
        changes: dict[str, list[tuple[str, list[bytes | str]]]],
        controls: object = None,
    ) -> bool:
        del dn, changes, controls
        return self._modify_result


class LdapUtilTest(UDSTestCase):
    def test_add_raises_already_exists_for_duplicate_entry(self) -> None:
        connection = FakeLDAPConnection(
            {"result": 68, "description": "entryAlreadyExists", "message": "entry already exists"},
            add_result=False,
        )

        with self.assertRaises(ldaputil.ALREADY_EXISTS):
            ldaputil.add(connection, "cn=test,dc=example,dc=com", attributes={"cn": ["test"]})  # type: ignore[arg-type]

    def test_modify_raises_already_exists_for_duplicate_member(self) -> None:
        connection = FakeLDAPConnection(
            {"result": 20, "description": "attributeOrValueExists", "message": "value already exists"},
            modify_result=False,
        )

        with self.assertRaises(ldaputil.ALREADY_EXISTS):
            ldaputil.modify(
                connection,  # type: ignore[arg-type]
                "cn=group,dc=example,dc=com",
                {"member": [(ldaputil.MODIFY_ADD, ["cn=machine,dc=example,dc=com"])]},
            )

    def test_add_raises_ldap_error_for_non_duplicate_failure(self) -> None:
        connection = FakeLDAPConnection(
            {"result": 50, "description": "insufficientAccessRights", "message": "access denied"},
            add_result=False,
        )

        with self.assertRaises(ldaputil.LDAPError):
            ldaputil.add(connection, "cn=test,dc=example,dc=com", attributes={"cn": ["test"]})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Helpers used by both the referral tests and the connection() tests
# ---------------------------------------------------------------------------


class _StubEntry:
    """Stand-in for an ldap3 ``Entry`` exposing ``entry_dn`` and attrs.

    Only the attributes the generator touches are implemented; that's
    enough for the tests below.
    """

    def __init__(
        self,
        dn: str,
        attrs: dict[str, list[str]],
    ) -> None:
        self.entry_dn: str = dn
        self._attrs: dict[str, list[str]] = attrs

    def __contains__(self, key: str) -> bool:
        return key in self._attrs

    def __getitem__(self, key: str) -> typing.Any:
        return _AttrValue(self._attrs.get(key, []))


class _AttrValue:
    def __init__(self, values: list[str]) -> None:
        self.values: list[str] = values


class _SearchFakeConn:
    """Fake ldap3 Connection that records ``search`` calls and yields entries.

    ``referrals`` populates ``result`` so we can exercise the referral path.
    """

    def __init__(
        self,
        entries: list[_StubEntry] | None = None,
        referrals: list[str] | None = None,
        response: list[dict[str, typing.Any]] | None = None,
    ) -> None:
        self.entries: list[_StubEntry] = entries or []
        self.response: list[dict[str, typing.Any]] = response or []
        self.result: dict[str, typing.Any] = {"result": 0, "description": "success"}
        if referrals is not None:
            self.result["referrals"] = referrals
        self.search_calls: list[dict[str, typing.Any]] = []

    def search(
        self,
        *,
        search_base: str,
        search_filter: str,
        search_scope: typing.Any,
        attributes: typing.Any,
        size_limit: int,
    ) -> bool:
        self.search_calls.append(
            {
                "search_base": search_base,
                "search_filter": search_filter,
                "search_scope": search_scope,
                "attributes": list(attributes),
                "size_limit": size_limit,
            }
        )
        return True


# ---------------------------------------------------------------------------
# LDAPReferralError + raise_on_referrals
# ---------------------------------------------------------------------------


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


class AsDictReferralsTest(UDSTestCase):
    def test_default_drops_referrals_silently(self) -> None:
        """``raise_on_referrals`` defaults to False for backwards compat."""
        conn: ldaputil.LDAPConnection = _SearchFakeConn(  # type: ignore[arg-type]
            entries=[],
            referrals=["ldap://other-dc/dc=corp,dc=local"],
        )
        rows: list[ldaputil.LDAPResultType] = list(ldaputil.as_dict(conn, "dc=corp,dc=local", "(uid=alice)"))
        self.assertEqual(rows, [])

    def test_raise_on_referrals_surfaces_uris(self) -> None:
        conn: ldaputil.LDAPConnection = _SearchFakeConn(  # type: ignore[arg-type]
            entries=[],
            referrals=["ldap://other-dc/dc=corp,dc=local", "ldap://other-dc2/dc=corp,dc=local"],
        )
        with self.assertRaises(ldaputil.LDAPReferralError) as ctx:
            list(
                ldaputil.as_dict(
                    conn,
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
        conn: ldaputil.LDAPConnection = _SearchFakeConn(  # type: ignore[arg-type]
            entries=[
                _StubEntry(
                    "cn=alice,dc=corp,dc=local",
                    {"uid": ["alice"], "cn": ["Alice"]},
                )
            ],
            referrals=None,
        )
        rows: list[ldaputil.LDAPResultType] = list(
            ldaputil.as_dict(
                conn,  # type: ignore[arg-type]
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
        conn: ldaputil.LDAPConnection = _SearchFakeConn(  # type: ignore[arg-type]
            entries=[
                _StubEntry(
                    "cn=alice,dc=corp,dc=local",
                    {"uid": ["alice"]},
                )
            ],
            referrals=["ldap://child-dc/dc=child,dc=corp,dc=local"],
        )
        with self.assertRaises(ldaputil.LDAPReferralError) as ctx:
            list(
                ldaputil.as_dict(
                    conn,
                    "dc=corp,dc=local",
                    "(uid=*)",
                    attributes=["uid"],
                    raise_on_referrals=True,
                )
            )
        self.assertEqual(ctx.exception.partial_results[0]["uid"], ["alice"])

    def test_search_result_references_are_detected_on_success(self) -> None:
        conn: ldaputil.LDAPConnection = _SearchFakeConn(  # type: ignore[arg-type]
            response=[
                {
                    "type": "searchResRef",
                    "uri": ["ldap://child-dc/dc=child,dc=corp,dc=local"],
                }
            ]
        )
        with self.assertRaises(ldaputil.LDAPReferralError) as ctx:
            list(
                ldaputil.as_dict(
                    conn,
                    "dc=corp,dc=local",
                    "(uid=*)",
                    raise_on_referrals=True,
                )
            )
        self.assertEqual(
            ctx.exception.referrals,
            ["ldap://child-dc/dc=child,dc=corp,dc=local"],
        )


class FirstReferralsTest(UDSTestCase):
    def test_propagates_referral_error(self) -> None:
        conn: ldaputil.LDAPConnection = _SearchFakeConn(  # type: ignore[arg-type]
            entries=[],
            referrals=["ldap://other-dc/dc=corp,dc=local"],
        )
        with self.assertRaises(ldaputil.LDAPReferralError):
            ldaputil.first(
                conn,  # type: ignore[arg-type]
                "dc=corp,dc=local",
                "user",
                "uid",
                "alice",
                raise_on_referrals=True,
            )

    def test_returns_none_on_empty_when_no_referrals(self) -> None:
        conn: ldaputil.LDAPConnection = _SearchFakeConn(  # type: ignore[arg-type]
            entries=[],
            referrals=None,
        )
        self.assertIsNone(
            ldaputil.first(
                conn,  # type: ignore[arg-type]
                "dc=corp,dc=local",
                "user",
                "uid",
                "alice",
                raise_on_referrals=True,
            )
        )

    def test_default_still_returns_none_on_empty(self) -> None:
        conn: ldaputil.LDAPConnection = _SearchFakeConn(  # type: ignore[arg-type]
            entries=[],
            referrals=["ldap://other-dc/dc=corp,dc=local"],
        )
        # Without raise_on_referrals, referrals are silently dropped and
        # ``first`` behaves like before: returns None on empty result.
        self.assertIsNone(
            ldaputil.first(
                conn,  # type: ignore[arg-type]
                "dc=corp,dc=local",
                "user",
                "uid",
                "alice",
            )
        )


# ---------------------------------------------------------------------------
# connection() preserves the LDAP result message so callers can key off
# sub-status codes like 773.
# ---------------------------------------------------------------------------


class _BindFakeConn:
    """Minimal stand-in for an ldap3 ``Connection`` driving the bind path."""

    def __init__(
        self,
        *,
        bind_ok: bool,
        result: dict[str, typing.Any] | None = None,
    ) -> None:
        self._bind_ok: bool = bind_ok
        # ``Connection.result`` is a property in ldap3 that returns a dict;
        # here it's a plain attribute because nothing in ``connection()``
        # calls it as a property (only ``.get`` on it).
        self.result: dict[str, typing.Any] = result if result is not None else {}
        self.opened: bool = False
        self.bound: bool = False

    def open(self) -> None:
        self.opened = True

    def bind(self) -> bool:
        self.bound = True
        return self._bind_ok


class ConnectionBindMessageTest(UDSTestCase):
    """``connection()`` must surface the LDAP result ``message`` on bind failure.

    The AD authenticator's ``check_password`` keys off substrings such as
    ``AcceptSecurityContext error, data 773`` to redirect users to a
    password changer. If ``connection()`` swallows that text the redirect
    never fires — see the bug report behind these tests.
    """

    def _patch_connection_factory(
        self,
        fake: _BindFakeConn,
    ) -> tuple[typing.Any, typing.Any]:
        return (
            mock.patch.object(ldaputil, "Server", return_value=mock.MagicMock()),
            mock.patch.object(ldaputil, "Connection", return_value=fake),
        )

    def test_failed_bind_includes_ldap_message(self) -> None:
        fake: _BindFakeConn = _BindFakeConn(
            bind_ok=False,
            result={
                "result": 49,
                "description": "invalidCredentials",
                "message": ("80090308: LdapErr: DSID-0C0903A9, comment: AcceptSecurityContext error, data 773, v3830"),
            },
        )
        server_patch, conn_patch = self._patch_connection_factory(fake)
        with server_patch, conn_patch:
            with self.assertRaises(ldaputil.LDAPError) as ctx:
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

    def test_failed_bind_without_message_still_raises(self) -> None:
        fake: _BindFakeConn = _BindFakeConn(bind_ok=False, result={"result": 49})
        server_patch, conn_patch = self._patch_connection_factory(fake)
        with server_patch, conn_patch:
            with self.assertRaises(ldaputil.LDAPError) as ctx:
                ldaputil.connection(
                    username="alice@corp.local",
                    passwd="bad",
                    host="dc.corp.local",
                    port=389,
                    use_ssl=False,
                    timeout=5,
                )
        # Falls back to the original prefix when ``result`` has no message.
        self.assertIn("Could not bind", str(ctx.exception))
        self.assertIn("dc.corp.local", str(ctx.exception))
        # And no trailing colon/empty fragment.
        self.assertFalse(str(ctx.exception).endswith(": "))

    def test_successful_bind_returns_connection(self) -> None:
        fake: _BindFakeConn = _BindFakeConn(bind_ok=True, result={"result": 0})
        server_patch, conn_patch = self._patch_connection_factory(fake)
        with server_patch, conn_patch:
            con: ldaputil.LDAPConnection = ldaputil.connection(  # type: ignore[arg-type]
                username="alice@corp.local",
                passwd="ok",
                host="dc.corp.local",
                port=389,
                use_ssl=False,
                timeout=5,
            )
        self.assertIs(con, fake)
        self.assertTrue(fake.opened)
        self.assertTrue(fake.bound)


if __name__ == "__main__":
    unittest.main()
