# -*- coding: utf-8 -*-
#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
"""
Author: dkmaster

Unit tests for ``uds.core.util.backoff.Backoff``.
"""

import typing
import unittest

from ...utils.test import UDSTestCase
from uds.core.util import cache
from uds.core.util.backoff import Backoff


class _DummyCache:
    """Minimal in-memory cache with the surface needed by ``Backoff``."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[typing.Any, int | None]] = {}

    def get(
        self,
        skey: str | bytes,
        default: typing.Any = None,
    ) -> typing.Any:
        key: str = skey if isinstance(skey, str) else skey.decode()
        entry: tuple[typing.Any, int | None] | None = self._store.get(key)
        return default if entry is None else entry[0]

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


def _real_cache() -> cache.CacheLike:
    """A real ``Cache`` instance (we never call ``put`` with valid > TTL, so
    no DB row gets persisted beyond what the test does)."""
    return cache.Cache(owner="test.backoff", default_timeout=10)


class ConstructionTest(UDSTestCase):
    def test_rejects_empty_owner(self) -> None:
        with self.assertRaises(ValueError):
            Backoff(_DummyCache(), owner="")

    def test_rejects_non_positive_fail_time(self) -> None:
        with self.assertRaises(ValueError):
            Backoff(_DummyCache(), owner="x", fail_time=0)

    def test_rejects_max_smaller_than_fail(self) -> None:
        with self.assertRaises(ValueError):
            Backoff(_DummyCache(), owner="x", fail_time=60, max_time=30)

    def test_rejects_scale_below_one(self) -> None:
        with self.assertRaises(ValueError):
            Backoff(_DummyCache(), owner="x", scale=0.5)


class IsBadTest(UDSTestCase):
    def test_fresh_key_is_not_bad(self) -> None:
        bo = Backoff(_DummyCache(), owner="svc", fail_time=30, max_time=28800)
        self.assertFalse(bo.is_bad("h1:389"))
        self.assertEqual(bo.ttl("h1:389"), 0)

    def test_mark_bad_sets_flag(self) -> None:
        bo = Backoff(_DummyCache(), owner="svc", fail_time=30, max_time=28800)
        bo.mark_bad("h1:389")
        self.assertTrue(bo.is_bad("h1:389"))
        self.assertEqual(bo.ttl("h1:389"), 30)

    def test_clear_bad_removes_flag(self) -> None:
        bo = Backoff(_DummyCache(), owner="svc", fail_time=30, max_time=28800)
        bo.mark_bad("h1:389")
        bo.clear_bad("h1:389")
        self.assertFalse(bo.is_bad("h1:389"))
        self.assertEqual(bo.ttl("h1:389"), 0)

    def test_owners_are_isolated(self) -> None:
        c = _DummyCache()
        bo_a = Backoff(c, owner="svc.a", fail_time=30)
        bo_b = Backoff(c, owner="svc.b", fail_time=30)
        bo_a.mark_bad("h1:389")
        self.assertTrue(bo_a.is_bad("h1:389"))
        self.assertFalse(bo_b.is_bad("h1:389"))


class BackoffProgressionTest(UDSTestCase):
    def test_first_failure_seeds_with_fail_time(self) -> None:
        bo = Backoff(_DummyCache(), owner="svc", fail_time=30, max_time=28800)
        self.assertEqual(bo.mark_bad("h1:389"), 30)

    def test_consecutive_failures_double(self) -> None:
        bo = Backoff(_DummyCache(), owner="svc", fail_time=30, max_time=28800)
        # 30 -> 60 -> 120 -> 240
        self.assertEqual(bo.mark_bad("h1:389"), 30)
        self.assertEqual(bo.mark_bad("h1:389"), 60)
        self.assertEqual(bo.mark_bad("h1:389"), 120)
        self.assertEqual(bo.mark_bad("h1:389"), 240)

    def test_caps_at_max_time(self) -> None:
        bo = Backoff(_DummyCache(), owner="svc", fail_time=30, max_time=28800)
        for _ in range(15):
            bo.mark_bad("h1:389")
        self.assertEqual(bo.ttl("h1:389"), 28800)

    def test_custom_scale(self) -> None:
        bo = Backoff(_DummyCache(), owner="svc", fail_time=10, max_time=10000, scale=3.0)
        # 10 -> 30 -> 90 -> 270
        self.assertEqual(bo.mark_bad("h1:389"), 10)
        self.assertEqual(bo.mark_bad("h1:389"), 30)
        self.assertEqual(bo.mark_bad("h1:389"), 90)
        self.assertEqual(bo.mark_bad("h1:389"), 270)


class StaleEntryTest(UDSTestCase):
    def test_stale_cooldown_is_consumed_after_flag_expires(self) -> None:
        """The cooldown ghost must survive a TTL expiry of the ``bad`` flag."""
        c = _DummyCache()
        bo = Backoff(c, owner="svc", fail_time=30, max_time=28800)
        bo.mark_bad("h1:389")  # 30s
        # Simulate the ``bad`` flag TTL expiring in cache; cooldown lingers.
        c.remove("svc.bad.h1:389")
        self.assertFalse(bo.is_bad("h1:389"))
        self.assertEqual(bo.ttl("h1:389"), 30)
        # Next failure doubles from 30 -> 60, not from the seed.
        self.assertEqual(bo.mark_bad("h1:389"), 60)

    def test_clear_during_stale_state_wipes_everything(self) -> None:
        c = _DummyCache()
        bo = Backoff(c, owner="svc", fail_time=30, max_time=28800)
        bo.mark_bad("h1:389")
        c.remove("svc.bad.h1:389")  # flag expires
        bo.clear_bad("h1:389")
        self.assertFalse(bo.is_bad("h1:389"))
        self.assertEqual(bo.ttl("h1:389"), 0)
        # Next failure restarts from the seed.
        self.assertEqual(bo.mark_bad("h1:389"), 30)


class RealCacheTest(UDSTestCase):
    """Smoke test that the real ``Cache`` backend is also compatible."""

    def test_round_trip_on_real_cache(self) -> None:
        c = _real_cache()
        bo = Backoff(c, owner="svc.real", fail_time=30, max_time=28800)
        bo.mark_bad("h1:389")
        self.assertTrue(bo.is_bad("h1:389"))
        self.assertEqual(bo.ttl("h1:389"), 30)
        bo.clear_bad("h1:389")
        self.assertFalse(bo.is_bad("h1:389"))


if __name__ == "__main__":
    unittest.main()
