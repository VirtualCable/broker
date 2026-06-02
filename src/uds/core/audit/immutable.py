# -*- coding: utf-8 -*-
#
# Copyright (c) 2012-2025 Virtual Cable S.L.U.
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

Immutable audit log backed by a hash chain (blockchain-like).

Each entry's hash is computed from::

    SHA-256(previous_hash | stamp_us | sequence | data_len | data)

The genesis entry is anchored to a random nonce + an external RFC 3161
timestamp token.  The nonce (32 random bytes, 2^256 possibilities) makes it
impossible to precompute or regenerate the same seed, even with knowledge of
SECRET_KEY.  The RFC 3161 token proves the seed existed at a specific moment.

Periodic re-anchoring is handled by the ``ImmutableLogAnchorJob`` worker,
controlled by ``GlobalConfig.IMMUTABLE_LOG_REANCHOR`` (seconds).  The worker
inserts a re-anchor entry when the configured interval elapses since the
last anchor.

Genesis and re-anchor entries have ``anchor=True`` on the model.
Data layout summary::

    Genesis:   [nonce_len:2B][nonce:32B][rfc3161_token:TB]
    Re-anchor: [rfc3161_token:TB]  (stamped hash = previous_hash field)
    Normal:    <raw payload bytes>
"""

import hashlib
import logging
import os
import pickle
import struct
import typing
import collections.abc

from datetime import datetime

from django.conf import settings
from django.core.exceptions import ValidationError

from uds.models.immutable_log import ImmutableLog
from uds.core.audit.stamping import StampProvider, DummyStampProvider, RFC3161StampProvider
from uds.core.util import config
from uds.core.util.model import sql_now

logger = logging.getLogger(__name__)

HASH_ALGO = 'sha256'
HASH_SIZE = 32
NONCE_SIZE = 32  # 256 bits — 2^256 possibilities, brute-force infeasible


def content_to_bytes(obj: typing.Any) -> bytes:
    """Serialize a Python object to bytes for storage in the immutable log."""
    return pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)


def content_from_bytes(data: bytes) -> typing.Any:
    """Deserialize bytes back to a Python object (use only on trusted data)."""
    return pickle.loads(data)


def _pack_genesis_data(nonce: bytes, token: bytes) -> bytes:
    """Pack nonce and RFC 3161 token into genesis data."""
    return struct.pack('>H', len(nonce)) + nonce + token


def _unpack_genesis_data(data: bytes | memoryview) -> tuple[bytes, bytes]:
    """Unpack genesis data into (nonce, rfc3161_token)."""
    data = bytes(data)
    pos = 0
    nonce_len = struct.unpack_from('>H', data, pos)[0]
    pos += 2
    nonce = data[pos : pos + nonce_len]
    pos += nonce_len
    token = data[pos:]
    return nonce, token


# ======================================================================
# Immutable Logger
# ======================================================================


class ImmutableLogger:
    """
    Service to create, append, and verify the immutable log chain.

    Usage::

        from uds.core.audit.stamping import RFC3161StampProvider

        # Optionally configure the stamp provider (otherwise reads from GlobalConfig)
        ImmutableLogger.configure(stamp_provider=RFC3161StampProvider())

        # Append (auto-initializes if chain is empty)
        ImmutableLogger.append_object({'event': 'login', 'user': 'admin'})

        # Verify
        ok, msg = ImmutableLogger.verify()
    """

    # -- configuration -----------------------------------------------------

    _stamp_provider: StampProvider | None = None
    """Provider used for genesis and re-anchoring."""

    _last_hash: bytes | None = None
    _last_sequence: int | None = None

    @staticmethod
    def is_enabled() -> bool:
        """Check if the immutable log is enabled"""
        return config.GlobalConfig.IMMUTABLE_LOG_ENABLED.as_bool()

    @classmethod
    def configure(cls, stamp_provider: StampProvider | None = None) -> None:
        """
        Configure the logger before first use.

        Args:
            stamp_provider: Provider for RFC 3161 timestamping.
                            Defaults to reading from GlobalConfig
                            (TSA_PROVIDER_URL, etc.).
        """
        if stamp_provider is not None:
            cls._stamp_provider = stamp_provider

    @classmethod
    def _get_stamp_provider(cls) -> StampProvider:
        if cls._stamp_provider is None:
            if config.GlobalConfig.TSA_PROVIDER_URL.as_str():
                cls._stamp_provider = RFC3161StampProvider(
                    url=config.GlobalConfig.TSA_PROVIDER_URL.as_str(),
                    timeout=config.GlobalConfig.TSA_PROVIDER_TIMEOUT.as_int(),
                    verify_ssl=config.GlobalConfig.TSA_PROVIDER_VERIFY_SSL.as_bool(),
                )
            else:
                cls._stamp_provider = DummyStampProvider()

        return cls._stamp_provider

    @classmethod
    def _compute_hash(
        cls,
        previous_hash: bytes,
        stamp: datetime,
        sequence: int,
        data: bytes,
    ) -> bytes:
        hasher = hashlib.new(HASH_ALGO)
        hasher.update(previous_hash)
        hasher.update(struct.pack('>q', int(stamp.timestamp() * 1_000_000)))
        hasher.update(struct.pack('>Q', sequence))
        hasher.update(struct.pack('>I', len(data)))
        hasher.update(data)
        return hasher.digest()

    @classmethod
    def _genesis_seed(cls, nonce: bytes | None = None) -> tuple[bytes, bytes]:
        """Generate the genesis seed hash. Returns ``(seed, nonce)``."""
        if nonce is None:
            nonce = os.urandom(NONCE_SIZE)

        secret = settings.SECRET_KEY.encode('utf-8')
        seed = hashlib.new(HASH_ALGO, secret + nonce).digest()
        return seed, nonce

    @classmethod
    def _get_last(cls) -> tuple[int, bytes]:
        """Return ``(last_sequence, last_hash)`` or ``(0, genesis_seed)`` if empty."""
        if cls._last_hash is not None:
            return cls._last_sequence or 0, cls._last_hash

        try:
            last = ImmutableLog.objects.latest()
            cls._last_sequence = last.sequence
            cls._last_hash = bytes(last.entry_hash)  # pyrefly: ignore[unnecessary-type-conversion]
        except ImmutableLog.DoesNotExist:
            cls._last_sequence = 0
            cls._last_hash = cls._genesis_seed()[0]

        return cls._last_sequence or 0, cls._last_hash

    @classmethod
    def _invalidate_cache(cls) -> None:
        cls._last_hash = None
        cls._last_sequence = None

    @classmethod
    def _create_entry(
        cls, previous_hash: bytes, sequence: int, data: bytes, *, anchor: bool = False
    ) -> ImmutableLog:
        """Low-level: create an entry and update the cache."""
        now = sql_now()
        entry_hash = cls._compute_hash(previous_hash, now, sequence, data)

        entry = ImmutableLog.objects.create(
            sequence=sequence,
            stamp=now,
            anchor=anchor,
            previous_hash=previous_hash,
            data=data,
            entry_hash=entry_hash,
        )

        cls._last_sequence = entry.sequence
        cls._last_hash = entry_hash
        return entry

    # -- public API -------------------------------------------------------

    @classmethod
    def initialize(
        cls,
        stamp_provider: StampProvider | None = None,
    ) -> ImmutableLog:
        """
        Create the genesis (first) log entry.

        The genesis binds a random nonce to an RFC 3161 timestamp token.
        The nonce makes the seed unpredictable (2^256), and the token anchors
        it to a verifiable point in time.

        Args:
            stamp_provider: Provider for RFC 3161 timestamping.
                            If None, uses the configured provider
                            (or DummyStampProvider by default).
        Raises:
            ValidationError: If the chain is already initialized.
        """
        if ImmutableLog.objects.exists():
            raise ValidationError('ImmutableLog chain is already initialized.')

        provider = stamp_provider or cls._get_stamp_provider()

        seed, nonce = cls._genesis_seed()
        token = provider.stamp(seed)

        packed_data = _pack_genesis_data(nonce, token)
        entry = cls._create_entry(seed, 1, packed_data, anchor=True)

        logger.info(
            'ImmutableLog initialized: seed=%s nonce=%s... provider=%s',
            seed.hex()[:16],
            nonce.hex()[:16],
            type(provider).__name__,
        )
        return entry

    @classmethod
    def append(cls, data: bytes) -> ImmutableLog:
        """
        Append a new immutable log entry with raw binary data.

        Auto-initializes the chain if empty.
        """
        last_seq, last_hash = cls._get_last()

        if last_seq == 0:
            cls.initialize()

        # Re-read after possible initialization
        last_seq, last_hash = cls._get_last()

        entry = cls._create_entry(last_hash, last_seq + 1, data)

        logger.debug('ImmutableLog append: #%d hash=%s', entry.sequence, entry.entry_hash.hex()[:16])
        return entry

    @classmethod
    def append_object(cls, obj: typing.Any) -> ImmutableLog:
        """Append a log entry, serializing ``obj`` via pickle."""
        return cls.append(content_to_bytes(obj))

    @classmethod
    def create_anchor(cls, stamped_hash: bytes) -> ImmutableLog:
        """
        Insert a re-anchor entry that timestamps ``stamped_hash``.

        Called by the ``ImmutableLogAnchorJob`` worker.
        """
        provider = cls._get_stamp_provider()
        token = provider.stamp(stamped_hash)

        last_seq, last_hash = cls._get_last()
        entry = cls._create_entry(last_hash, last_seq + 1, token, anchor=True)

        logger.debug(
            'Re-anchor #%d: stamped=%s token_len=%d provider=%s',
            entry.sequence,
            stamped_hash.hex()[:16],
            len(token),
            type(provider).__name__,
        )
        return entry

    @classmethod
    def verify(
        cls,
        genesis_provider: StampProvider | None = None,
        reanchor_provider: StampProvider | None = None,
    ) -> tuple[bool, str]:
        """
        Verify the entire chain integrity.

        Checks:
          1. Genesis seed is recoverable (nonce + SECRET_KEY).
          2. Genesis RFC 3161 token verifies (if provider given).
          3. Every entry hash matches its computed hash.
          4. Every ``previous_hash`` chains correctly.
          5. Every re-anchor TSA token verifies (if provider given).

        Args:
            genesis_provider: If given, verifies the genesis token.
            reanchor_provider: If given, verifies re-anchor tokens.
                Defaults to the configured ``_stamp_provider`` if None
                and ``genesis_provider`` is also None.

        Returns:
            ``(ok, message)``.
        """
        entries = list(ImmutableLog.objects.order_by('sequence'))

        if not entries:
            return False, 'No entries in the log (chain not initialized).'

        if reanchor_provider is None and genesis_provider is None:
            reanchor_provider = cls._stamp_provider

        genesis = entries[0]

        # Verify genesis seed
        try:
            nonce, token = _unpack_genesis_data(genesis.data)
        except (struct.error, IndexError):
            return False, 'Genesis data is malformed (cannot unpack nonce/token).'

        secret = settings.SECRET_KEY.encode('utf-8')
        expected_seed = hashlib.new(HASH_ALGO, secret + nonce).digest()
        stored_seed = bytes(genesis.previous_hash.tobytes() if hasattr(genesis.previous_hash, 'tobytes') else genesis.previous_hash)  # type: ignore

        if stored_seed != expected_seed:
            return False, (
                f'Genesis seed mismatch: '
                f'stored={stored_seed.hex()} '
                f'expected={expected_seed.hex()} '
                f'(nonce={nonce.hex()[:16]}...)'
            )

        # Verify genesis TSA token
        if genesis_provider is not None:
            if not genesis_provider.verify(expected_seed, token):
                return False, 'Genesis RFC 3161 token verification failed.'

        # Walk the chain
        expected_previous = bytes(genesis.entry_hash)  # pyrefly: ignore[unnecessary-type-conversion]
        reanchor_count = 0

        for entry in entries[1:]:
            computed = cls._compute_hash(
                bytes(entry.previous_hash),  # pyrefly: ignore[unnecessary-type-conversion]
                entry.stamp,
                entry.sequence,
                bytes(entry.data),  # pyrefly: ignore[unnecessary-type-conversion]
            )

            if computed != bytes(entry.entry_hash):  # pyrefly: ignore[unnecessary-type-conversion]
                return False, (
                    f'Hash mismatch at entry #{entry.sequence}: '
                    f'stored={entry.entry_hash.hex()} computed={computed.hex()}'
                )

            if bytes(entry.previous_hash) != expected_previous:  # pyrefly: ignore[unnecessary-type-conversion]
                return False, (
                    f'Chain break at entry #{entry.sequence}: '
                    f'expected prev={expected_previous.hex()} '
                    f'got={entry.previous_hash.hex()}'
                )

            # Re-anchor verification
            if entry.anchor:
                if reanchor_provider is not None:
                    if not reanchor_provider.verify(
                        expected_previous, bytes(entry.data)  # pyrefly: ignore[unnecessary-type-conversion]
                    ):
                        return False, (f'Re-anchor TSA verification failed at entry #{entry.sequence}')
                reanchor_count += 1
                logger.debug('Re-anchor #%d verified OK', entry.sequence)

            expected_previous = bytes(entry.entry_hash)  # pyrefly: ignore[unnecessary-type-conversion]

        msg = f'Chain verified: {len(entries)} entries'
        if reanchor_count:
            msg += f', {reanchor_count} re-anchor(s) OK'
        msg += ', all hashes match.'
        return True, msg

    @classmethod
    def stats(cls) -> dict[str, typing.Any]:
        """
        Return summary information about the chain.

        Keys:
            total_entries, anchor_count, normal_count, reanchor_config.
        """
        total = ImmutableLog.objects.count()
        anchor_count = ImmutableLog.objects.filter(anchor=True).count()
        seconds = config.GlobalConfig.IMMUTABLE_LOG_REANCHOR.as_int()

        return {
            'total_entries': total,
            'anchor_count': anchor_count,
            'normal_count': total - anchor_count,
            'reanchor_config': f'{seconds}s' if seconds > 0 else 'disabled',
        }

    @classmethod
    def verified_entries(
        cls,
        genesis_provider: StampProvider | None = None,
        reanchor_provider: StampProvider | None = None,
    ) -> collections.abc.Iterator[ImmutableLog]:
        """
        Generator that yields entries while the chain remains valid.

        Stops at the first integrity violation.
        """
        entries = ImmutableLog.objects.order_by('sequence')
        try:
            first = entries[0]
        except IndexError:
            return

        if reanchor_provider is None and genesis_provider is None:
            reanchor_provider = cls._stamp_provider

        # Verify genesis
        try:
            nonce, token = _unpack_genesis_data(first.data)
        except (struct.error, IndexError):
            logger.error('Genesis data malformed')
            return

        secret = settings.SECRET_KEY.encode('utf-8')
        expected_seed = hashlib.new(HASH_ALGO, secret + nonce).digest()

        if bytes(first.previous_hash) != expected_seed:  # pyrefly: ignore[unnecessary-type-conversion]
            logger.error('Genesis seed mismatch')
            return

        if genesis_provider and not genesis_provider.verify(expected_seed, token):
            logger.error('Genesis token verification failed')
            return

        yield first
        expected_previous = bytes(first.entry_hash)  # pyrefly: ignore[unnecessary-type-conversion]

        for entry in entries[1:]:
            if bytes(entry.previous_hash) != expected_previous:  # pyrefly: ignore[unnecessary-type-conversion]
                logger.error('Chain break at entry #%d', entry.sequence)
                return

            computed = cls._compute_hash(
                bytes(entry.previous_hash),  # pyrefly: ignore[unnecessary-type-conversion]
                entry.stamp,
                entry.sequence,
                bytes(entry.data),  # pyrefly: ignore[unnecessary-type-conversion]
            )
            if computed != bytes(entry.entry_hash):  # pyrefly: ignore[unnecessary-type-conversion]
                logger.error('Hash mismatch at entry #%d', entry.sequence)
                return

            # Verify re-anchor
            if entry.anchor:
                if reanchor_provider and not reanchor_provider.verify(
                    expected_previous, bytes(entry.data) # pyrefly: ignore[unnecessary-type-conversion]
                ):  
                    logger.error('Re-anchor TSA verification failed at entry #%d', entry.sequence)
                    return

            expected_previous = entry.entry_hash
            yield entry
