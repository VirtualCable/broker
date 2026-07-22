# -*- coding: utf-8 -*-
#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
"""
Author: dkmaster

Tests for the "DC orchestration" helpers in ``uds.core.util.ldaputil``:
``connect_with_pool`` and ``dn_from_domain``. Pure unit tests against
``ldaputil.connection``, monkeypatched. TCP reachability is delegated to
``uds.core.util.net.test_connectivity`` (covered by its own tests).
"""

import typing
import collections.abc
import unittest

from ...utils.test import UDSTestCase
from uds.core.util import ldaputil


class _DummyCache:
    """Tiny in-memory cache with the surface needed by ``connect_with_pool``."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[typing.Any, int | None]] = {}

    def get(
        self,
        skey: str | bytes,
        default: typing.Any = None,
        *,
        with_validity: bool = False,
    ) -> typing.Any:
        key: str = skey if isinstance(skey, str) else skey.decode()
        entry: tuple[typing.Any, int | None] | None = self._store.get(key)
        if entry is None:
            if with_validity:
                return (default, None)
            return default
        if with_validity:
            return entry
        return entry[0]

    def put(
        self,
        skey: str | bytes,
        value: typing.Any,
        validity: int | None = None,
    ) -> None:
        key = skey if isinstance(skey, str) else skey.decode()
        self._store[key] = (value, validity)

    def remove(self, skey: str | bytes) -> bool:
        key = skey if isinstance(skey, str) else skey.decode()
        return self._store.pop(key, None) is not None


class _DummyConn:
    """Stand-in for an ldap3.Connection."""


class DnFromDomainTest(UDSTestCase):
    def test_simple(self) -> None:
        self.assertEqual(ldaputil.dn_from_domain("a.b.c"), "dc=a,dc=b,dc=c")

    def test_single_label(self) -> None:
        self.assertEqual(ldaputil.dn_from_domain("local"), "dc=local")

    def test_strips_spaces(self) -> None:
        self.assertEqual(ldaputil.dn_from_domain(" a . b "), "dc=a,dc=b")

    def test_empty(self) -> None:
        self.assertEqual(ldaputil.dn_from_domain(""), "")
        self.assertEqual(ldaputil.dn_from_domain("   "), "")


class ConnectWithPoolTest(UDSTestCase):
    """Drives ``connect_with_pool`` with a monkeypatched ``ldaputil.connection``
    and a test-injected ``CacheLike`` so we can pre-seed preferred/bad
    entries without touching the shared process-wide ``Cache('ldap')``."""

    def _patch_connection(
        self,
        behavior: dict[tuple[str, int], collections.abc.Callable[[], typing.Any]],
    ) -> tuple[collections.abc.Callable[[], None], list[tuple[str, int]]]:
        calls: list[tuple[str, int]] = []
        original = ldaputil.connection

        def fake_connection(
            user: str,
            passwd: str,
            host: str,
            *,
            port: int = -1,
            **kwargs: typing.Any,
        ) -> typing.Any:
            del passwd, user, kwargs
            calls.append((host, port))
            action: collections.abc.Callable[[], typing.Any] | None = behavior.get((host, port))
            if action is None:
                raise ldaputil.LDAPError(f"no behavior for {host}:{port}")
            result: typing.Any = action()
            if result is None:
                raise ldaputil.LDAPError(f"{host}:{port} refused")
            return result

        setattr(ldaputil, "connection", fake_connection)

        def _restore() -> None:
            setattr(ldaputil, "connection", original)

        return _restore, calls

    def test_picks_first_good(self) -> None:
        cache = _DummyCache()
        restore_conn, calls = self._patch_connection(
            behavior={("h1", 389): lambda: _DummyConn()},
        )
        try:
            con: typing.Any = ldaputil.connect_with_pool(
                user="u",
                password="p",
                hosts=[("h1", 389)],
                cache=cache,
                probe=False,
            )
        finally:
            restore_conn()
        self.assertIsInstance(con, _DummyConn)
        self.assertEqual(calls, [("h1", 389)])
        self.assertEqual(cache.get("ldap.preferred"), [("h1", 389)])

    def test_skips_bad_then_picks_next(self) -> None:
        cache = _DummyCache()
        cache.put("ldap.bad.h1:389", True, validity=60)
        restore_conn, calls = self._patch_connection(
            behavior={("h2", 389): lambda: _DummyConn()},
        )
        try:
            ldaputil.connect_with_pool(
                user="u",
                password="p",
                hosts=[("h1", 389), ("h2", 389)],
                cache=cache,
                probe=False,
            )
        finally:
            restore_conn()
        self.assertEqual(calls, [("h2", 389)])

    def test_marks_bad_on_failure(self) -> None:
        cache = _DummyCache()
        restore_conn, _ = self._patch_connection(
            behavior={
                ("h1", 389): lambda: (_ for _ in ()).throw(ldaputil.LDAPError("nope")),
                ("h2", 389): lambda: _DummyConn(),
            },
        )
        try:
            ldaputil.connect_with_pool(
                user="u",
                password="p",
                hosts=[("h1", 389), ("h2", 389)],
                cache=cache,
                probe=False,
            )
        finally:
            restore_conn()
        self.assertTrue(cache.get("ldap.bad.h1:389"))
        self.assertEqual(cache.get("ldap.preferred"), [("h2", 389)])

    def test_preferred_first(self) -> None:
        cache = _DummyCache()
        cache.put("ldap.preferred", [("h3", 389)], validity=3600)
        restore_conn, calls = self._patch_connection(
            behavior={
                ("h3", 389): lambda: _DummyConn(),
                ("h1", 389): lambda: _DummyConn(),
            },
        )
        try:
            ldaputil.connect_with_pool(
                user="u",
                password="p",
                hosts=[("h1", 389), ("h2", 389), ("h3", 389)],
                cache=cache,
                probe=False,
            )
        finally:
            restore_conn()
        # Preferred (h3) is tried first; if it succeeds no further host is
        # probed.
        self.assertEqual(calls, [("h3", 389)])

    def test_all_fail_raises(self) -> None:
        cache = _DummyCache()
        restore_conn, _ = self._patch_connection(
            behavior={
                ("h1", 389): lambda: (_ for _ in ()).throw(ldaputil.LDAPError("nope")),
            },
        )
        try:
            with self.assertRaises(ldaputil.LDAPError):
                ldaputil.connect_with_pool(
                    user="u",
                    password="p",
                    hosts=[("h1", 389)],
                    cache=cache,
                    probe=False,
                )
        finally:
            restore_conn()


if __name__ == "__main__":
    unittest.main()
