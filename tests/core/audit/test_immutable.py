# -*- coding: utf-8 -*-
#
# Copyright (c) 2025 Virtual Cable S.L.U.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright notice,
#      this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright notice,
#      this list of conditions and the following disclaimer in the documentation
#      and/or other materials provided with the distribution.
#    * Neither the name of Virtual Cable S.L.U. nor the names of its contributors
#      may be used to endorse or promote products derived from this software
#      without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""
Author: Adolfo Gómez, dkmaster at dkmon dot com

Tests for the immutable audit log.
"""

import hashlib
import os
import unittest.mock

from django.core.exceptions import ValidationError

from uds.core.audit.immutable import HASH_ALGO
from uds.core.audit.immutable import NONCE_SIZE
from uds.core.audit.immutable import ImmutableLogger
from uds.core.audit.immutable import _pack_genesis_data
from uds.core.audit.immutable import _unpack_genesis_data
from uds.core.audit.immutable import content_from_bytes
from uds.core.audit.immutable import content_to_bytes
from uds.core.audit.stamping import DummyStampProvider
from uds.models.immutable_log import HASH_SIZE
from uds.models.immutable_log import ImmutableLog

from ...utils.test import UDSTestCase


class ImmutableLogModelTest(UDSTestCase):
    """Tests for the ImmutableLog Django model."""

    def test_create_entry(self) -> None:
        entry = ImmutableLog.objects.create(
            sequence=1,
            previous_hash=b"\x00" * HASH_SIZE,
            data=b"hello",
            entry_hash=hashlib.new(HASH_ALGO, b"test").digest(),
        )
        self.assertEqual(entry.sequence, 1)
        self.assertFalse(entry.anchor)
        self.assertIsNotNone(entry.stamp)
        self.assertEqual(bytes(entry.previous_hash), b"\x00" * HASH_SIZE)
        self.assertEqual(bytes(entry.data), b"hello")

    def test_create_anchor_entry(self) -> None:
        entry = ImmutableLog.objects.create(
            sequence=1,
            anchor=True,
            previous_hash=b"\x01" * HASH_SIZE,
            data=b"anchor data",
            entry_hash=hashlib.new(HASH_ALGO, b"anchor").digest(),
        )
        self.assertTrue(entry.anchor)

    def test_cannot_update(self) -> None:
        entry = ImmutableLog.objects.create(
            sequence=99,
            previous_hash=b"\x00" * HASH_SIZE,
            data=b"original",
            entry_hash=hashlib.new(HASH_ALGO, b"one").digest(),
        )
        with self.assertRaises(ValidationError):
            entry.save()

    def test_cannot_delete(self) -> None:
        entry = ImmutableLog.objects.create(
            sequence=100,
            previous_hash=b"\x00" * HASH_SIZE,
            data=b"original",
            entry_hash=hashlib.new(HASH_ALGO, b"two").digest(),
        )
        with self.assertRaises(ValidationError):
            entry.delete()

    def test_repr(self) -> None:
        entry = ImmutableLog.objects.create(
            sequence=42,
            previous_hash=b"\x00" * HASH_SIZE,
            data=b"repr test",
            entry_hash=hashlib.new(HASH_ALGO, b"repr").digest(),
        )
        repr_str = repr(entry)
        self.assertIn("42", repr_str)
        self.assertIn("ImmutableLog", repr_str)


class PackingTest(UDSTestCase):
    """Tests for genesis data pack/unpack."""

    def test_roundtrip(self) -> None:
        nonce = os.urandom(NONCE_SIZE)
        token = os.urandom(128)
        packed = _pack_genesis_data(nonce, token)
        unpacked_nonce, unpacked_token = _unpack_genesis_data(packed)
        self.assertEqual(unpacked_nonce, nonce)
        self.assertEqual(unpacked_token, token)

    def test_empty_token(self) -> None:
        nonce = os.urandom(NONCE_SIZE)
        packed = _pack_genesis_data(nonce, b"")
        unpacked_nonce, unpacked_token = _unpack_genesis_data(packed)
        self.assertEqual(unpacked_nonce, nonce)
        self.assertEqual(unpacked_token, b"")

    def test_small_nonce(self) -> None:
        nonce = b"\x01\x02\x03"
        token = b"token"
        packed = _pack_genesis_data(nonce, token)
        unpacked_nonce, unpacked_token = _unpack_genesis_data(packed)
        self.assertEqual(unpacked_nonce, nonce)
        self.assertEqual(unpacked_token, token)


class SerializationTest(UDSTestCase):
    """Tests for content serialization."""

    def test_dict_roundtrip(self) -> None:
        original = {"t": "rest", "m": "GET", "p": "/adm/test", "c": 200, "i": "1.2.3.4", "u": "admin"}
        data = content_to_bytes(original)
        self.assertIsInstance(data, bytes)
        restored = content_from_bytes(data)
        self.assertEqual(restored, original)

    def test_login_dict_roundtrip(self) -> None:
        original = {"t": "login", "a": "AD", "u": "jdoe", "i": "10.0.0.1", "o": "Linux", "r": "Logged in", "e": False}
        restored = content_from_bytes(content_to_bytes(original))
        self.assertEqual(restored, original)

    def test_logout_dict_roundtrip(self) -> None:
        original = {"t": "logout", "u": "jdoe", "a": "AD", "i": "10.0.0.1"}
        restored = content_from_bytes(content_to_bytes(original))
        self.assertEqual(restored, original)


class ImmutableLoggerTest(UDSTestCase):
    """Integration tests for the ImmutableLogger chain."""

    def setUp(self) -> None:
        super().setUp()
        ImmutableLogger._invalidate_cache()
        # Ensure we use the dummy provider for tests
        ImmutableLogger.configure(stamp_provider=DummyStampProvider())

    def tearDown(self) -> None:
        ImmutableLogger._invalidate_cache()
        super().tearDown()

    def test_initialize_creates_genesis(self) -> None:
        genesis = ImmutableLogger.initialize()
        self.assertEqual(genesis.sequence, 1)
        self.assertTrue(genesis.anchor)
        self.assertIsNotNone(genesis.stamp)
        self.assertIsNotNone(genesis.entry_hash)
        self.assertEqual(ImmutableLog.objects.count(), 1)

    def test_initialize_twice_fails(self) -> None:
        ImmutableLogger.initialize()
        with self.assertRaises(ValidationError):
            ImmutableLogger.initialize()

    def test_append_auto_initializes(self) -> None:
        # Table should be empty at start of test (UDSTestCase rolls back)
        entry = ImmutableLogger.append(b"first log entry")
        self.assertEqual(ImmutableLog.objects.count(), 2)  # genesis + entry
        self.assertEqual(entry.sequence, 2)
        self.assertFalse(entry.anchor)
        self.assertEqual(bytes(entry.data), b"first log entry")

    def test_append_object(self) -> None:
        obj = {"event": "test", "user": "admin"}
        entry = ImmutableLogger.append_object(obj)
        self.assertEqual(content_from_bytes(bytes(entry.data)), obj)

    def test_chain_verify_ok(self) -> None:
        ImmutableLogger.append(b"entry 1")
        ImmutableLogger.append(b"entry 2")
        ImmutableLogger.append(b"entry 3")

        ok, msg = ImmutableLogger.verify()
        self.assertTrue(ok, msg=msg)
        self.assertIn("Chain verified", msg)
        self.assertIn("all hashes match", msg)

    def test_chain_verify_genesis_provider(self) -> None:
        ImmutableLogger.initialize(stamp_provider=DummyStampProvider())
        ImmutableLogger.append(b"entry")

        ok, msg = ImmutableLogger.verify(genesis_provider=DummyStampProvider())
        self.assertTrue(ok, msg=msg)

    def test_verify_fails_with_tampered_data(self) -> None:
        ImmutableLogger.append(b"original")
        ImmutableLogger.append(b"to tamper")

        # Tamper with the last entry's data directly in DB (bypass custom save)
        entry = ImmutableLog.objects.latest()
        entry.data = b"tampered!"
        from django.db import models

        models.Model.save(entry)

        ok, msg = ImmutableLogger.verify()
        self.assertFalse(ok)
        self.assertIn("Hash mismatch", msg)

    def test_verified_entries_generator(self) -> None:
        ImmutableLogger.append(b"a")
        ImmutableLogger.append(b"b")
        ImmutableLogger.append(b"c")

        entries = list(ImmutableLogger.verified_entries())
        self.assertEqual(len(entries), 4)  # genesis + 3

        for entry in entries:
            self.assertIsInstance(entry, ImmutableLog)

    def test_verified_entries_stops_on_tamper(self) -> None:
        ImmutableLogger.append(b"a")
        ImmutableLogger.append(b"b")
        ImmutableLogger.append(b"c")

        # Tamper with the second entry
        target = ImmutableLog.objects.get(sequence=3)
        target.data = b"corrupted"
        from django.db import models

        models.Model.save(target)

        entries = list(ImmutableLogger.verified_entries())
        # Should stop at sequence 3 (hash mismatch)
        self.assertEqual(len(entries), 2)  # genesis + entry #2 (sequences 1-2)

    def test_stats(self) -> None:
        ImmutableLogger.append(b"a")
        ImmutableLogger.append(b"b")

        stats = ImmutableLogger.stats()
        self.assertEqual(stats["total_entries"], 3)  # genesis + 2
        self.assertEqual(stats["anchor_count"], 1)  # only genesis
        self.assertEqual(stats["normal_count"], 2)

    def test_is_enabled_off_by_default(self) -> None:
        # Default config value is '0' → disabled
        with unittest.mock.patch.object(ImmutableLogger, "is_enabled", return_value=False):
            self.assertFalse(ImmutableLogger.is_enabled())

    def test_is_enabled_on(self) -> None:
        with unittest.mock.patch.object(ImmutableLogger, "is_enabled", return_value=True):
            self.assertTrue(ImmutableLogger.is_enabled())

    def test_compute_hash_deterministic(self) -> None:
        from datetime import datetime
        from datetime import timezone

        stamp = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        hash1 = ImmutableLogger._compute_hash(b"\x00" * 32, stamp, 1, b"data")
        hash2 = ImmutableLogger._compute_hash(b"\x00" * 32, stamp, 1, b"data")
        self.assertEqual(hash1, hash2)

    def test_compute_hash_differs_on_data_change(self) -> None:
        from datetime import datetime
        from datetime import timezone

        stamp = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        hash1 = ImmutableLogger._compute_hash(b"\x00" * 32, stamp, 1, b"data_a")
        hash2 = ImmutableLogger._compute_hash(b"\x00" * 32, stamp, 1, b"data_b")
        self.assertNotEqual(hash1, hash2)


class DummyStampProviderTest(UDSTestCase):
    """Tests for the dummy stamp provider."""

    def test_stamp_and_verify(self) -> None:
        provider = DummyStampProvider()
        hash_data = hashlib.sha256(b"test data").digest()
        token = provider.stamp(hash_data)
        self.assertTrue(provider.verify(hash_data, token))

    def test_verify_fails_with_wrong_hash(self) -> None:
        provider = DummyStampProvider()
        hash_a = hashlib.sha256(b"data a").digest()
        hash_b = hashlib.sha256(b"data b").digest()
        token = provider.stamp(hash_a)
        self.assertFalse(provider.verify(hash_b, token))

    def test_verify_fails_with_truncated_token(self) -> None:
        provider = DummyStampProvider()
        hash_data = hashlib.sha256(b"test").digest()
        token = provider.stamp(hash_data)
        self.assertFalse(provider.verify(hash_data, token[:10]))


class CreateAnchorTest(UDSTestCase):
    """Tests for explicit create_anchor() — called by the worker."""

    def setUp(self) -> None:
        super().setUp()
        ImmutableLogger._invalidate_cache()
        ImmutableLogger.configure(stamp_provider=DummyStampProvider())

    def tearDown(self) -> None:
        ImmutableLogger._invalidate_cache()
        super().tearDown()

    def test_create_anchor_inserts_entry(self) -> None:
        ImmutableLogger.append(b"entry 1")
        ImmutableLogger.append(b"entry 2")

        # Explicitly create a re-anchor — same as what the worker does
        last = ImmutableLog.objects.latest()
        anchor = ImmutableLogger.create_anchor(bytes(last.entry_hash))

        self.assertTrue(anchor.anchor)
        self.assertEqual(anchor.sequence, 4)  # genesis(1) + entry(2) + entry(3) + anchor(4)
        self.assertEqual(ImmutableLog.objects.count(), 4)
        self.assertEqual(ImmutableLog.objects.filter(anchor=True).count(), 2)  # genesis + re-anchor

    def test_create_anchor_chain_verifies(self) -> None:
        ImmutableLogger.append(b"entry 1")
        last = ImmutableLog.objects.latest()
        ImmutableLogger.create_anchor(bytes(last.entry_hash))

        ok, msg = ImmutableLogger.verify()
        self.assertTrue(ok, msg=msg)
