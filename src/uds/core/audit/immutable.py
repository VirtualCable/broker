# -*- coding: utf-8 -*-
#
# Copyright (c) 2025 Virtual Cable S.L.U.
# All rights reserved.
#

"""
Author: Adolfo Gómez, dkmaster at dkmon dot com

Immutable audit log backed by a hash chain (blockchain-like).

Each entry's hash is computed from::

    SHA-256(previous_hash | stamp_us | sequence | data_len | data)

The genesis entry is anchored to a random nonce + an external RFC 3161
timestamp token.  The nonce (32 random bytes, 2^256 possibilities) makes it
impossible to precompute or regenerate the same seed, even with knowledge of
SECRET_KEY.  The RFC 3161 token proves the seed existed at a specific moment.

Periodic re-anchoring: every ``REANCHOR_INTERVAL`` normal entries, a
re-anchor entry is inserted automatically.  It stores the TSA token for
the previous entry's hash, freezing the chain up to that point.

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

from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone as dj_timezone

from uds.models.immutable_log import ImmutableLog
from uds.core.audit.stamping import StampProvider, DummyStampProvider, RFC3161StampProvider

logger = logging.getLogger(__name__)

HASH_ALGO = 'sha256'
HASH_SIZE = 32
NONCE_SIZE = 32  # 256 bits — 2^256 possibilities, brute-force infeasible


# ======================================================================
# Serialization helpers
# ======================================================================


def content_to_bytes(obj: typing.Any) -> bytes:
    """Serialize a Python object to bytes for storage in the immutable log."""
    return pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)


def content_from_bytes(data: bytes) -> bytes:
    """Deserialize bytes back to a Python object (use only on trusted data)."""
    return pickle.loads(data)


# ======================================================================
# Data packing helpers
# ======================================================================


def _pack_genesis_data(nonce: bytes, token: bytes) -> bytes:
    """Pack nonce and RFC 3161 token into genesis data."""
    return struct.pack('>H', len(nonce)) + nonce + token


def _unpack_genesis_data(data: bytes) -> tuple[bytes, bytes]:
    """Unpack genesis data into (nonce, rfc3161_token)."""
    pos = 0
    nonce_len = struct.unpack_from('>H', data, pos)[0]
    pos += 2
    nonce = data[pos:pos + nonce_len]
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

        # Configure the logger
        ImmutableLogger.configure(
            stamp_provider=RFC3161StampProvider(),
            reanchor_interval=100,
        )

        # Init chain
        ImmutableLogger.initialize()

        # Append (auto-initializes if chain is empty)
        ImmutableLogger.append_object({'event': 'login', 'user': 'admin'})

        # Verify
        ok, msg = ImmutableLogger.verify()
    """

    # -- configuration -----------------------------------------------------

    REANCHOR: int | timedelta = 0
    """
    Re-anchor trigger.

    - ``int``:  insert a re-anchor every N normal entries.
    - ``timedelta``:  insert a re-anchor after the given wall-clock interval
      since the last anchor was created.
    - ``0``:  disabled.
    """

    _stamp_provider: StampProvider | None = None
    """Provider used for genesis and re-anchoring."""

    # -- internal state ----------------------------------------------------

    _last_hash: bytes | None = None
    _last_sequence: int | None = None
    _entries_since_last_anchor: int = 0
    _last_anchor_stamp: datetime | None = None

    # -- configuration API -------------------------------------------------

    @classmethod
    def configure(
        cls,
        stamp_provider: StampProvider | None = None,
        reanchor: int | timedelta | None = None,
    ) -> None:
        """
        Configure the logger before first use.

        Args:
            stamp_provider: Provider for RFC 3161 timestamping.
                            Defaults to :class:`DummyStampProvider`.
            reanchor: How often to insert a re-anchor entry.
                      ``int`` → every N normal entries.
                      ``timedelta`` → after that wall-clock interval.
                      ``0`` → disabled.
        """
        if stamp_provider is not None:
            cls._stamp_provider = stamp_provider
        if reanchor is not None:
            cls.REANCHOR = reanchor

    @classmethod
    def _get_stamp_provider(cls) -> StampProvider:
        if cls._stamp_provider is None:
            if getattr(settings, 'IMMUTABLE_LOG_STAMP_PROVIDER', None) == 'rfc3161':
                cls._stamp_provider = RFC3161StampProvider(
                    url=getattr(settings, 'IMMUTABLE_LOG_TSA_URL', 'http://localhost:8080/tsa'),
                    timeout=getattr(settings, 'IMMUTABLE_LOG_TSA_TIMEOUT', 30),
                    verify_ssl=getattr(settings, 'IMMUTABLE_LOG_TSA_VERIFY_SSL', True),
                )
            else:
                cls._stamp_provider = DummyStampProvider()
        return cls._stamp_provider

    # -- internal helpers -------------------------------------------------

    @classmethod
    def _compute_hash(
        cls,
        previous_hash: bytes,
        stamp: datetime,
        sequence: int,
        data: bytes,
    ) -> bytes:
        hasher = hashlib.new(HASH_ALGO)
        hasher.update(previous_hash)                                         # 32 bytes
        hasher.update(struct.pack('>q', int(stamp.timestamp() * 1_000_000)))  # 8 bytes (micros)
        hasher.update(struct.pack('>Q', sequence))                            # 8 bytes (uint64)
        hasher.update(struct.pack('>I', len(data)))                           # 4 bytes (data length)
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
            cls._last_hash = last.entry_hash
        except ImmutableLog.DoesNotExist:
            cls._last_sequence = 0
            cls._last_hash = cls._genesis_seed()[0]

        return cls._last_sequence or 0, cls._last_hash

    @classmethod
    def _invalidate_cache(cls) -> None:
        cls._last_hash = None
        cls._last_sequence = None
        cls._entries_since_last_anchor = 0
        cls._last_anchor_stamp = None

    @classmethod
    def _should_reanchor(cls) -> bool:
        if cls.REANCHOR == 0:
            return False
        if isinstance(cls.REANCHOR, int):
            return cls._entries_since_last_anchor >= cls.REANCHOR
        # timedelta: check wall-clock elapsed
        if cls._last_anchor_stamp is None:
            cls._last_anchor_stamp = dj_timezone.now()
            return False
        return dj_timezone.now() - cls._last_anchor_stamp >= cls.REANCHOR

    @classmethod
    def _create_entry(
        cls, previous_hash: bytes, sequence: int, data: bytes, *, anchor: bool = False
    ) -> ImmutableLog:
        """Low-level: create an entry and update the cache."""
        now = dj_timezone.now()
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

    @classmethod
    def _append_reanchor(cls, stamped_hash: bytes) -> ImmutableLog:
        """Insert a re-anchor entry that timestamps ``stamped_hash``."""
        provider = cls._get_stamp_provider()
        token = provider.stamp(stamped_hash)

        last_seq, last_hash = cls._get_last()
        entry = cls._create_entry(last_hash, last_seq + 1, token, anchor=True)

        cls._last_anchor_stamp = entry.stamp
        cls._entries_since_last_anchor = 0

        logger.debug(
            'Re-anchor #%d: stamped=%s token_len=%d provider=%s',
            entry.sequence,
            stamped_hash.hex()[:16],
            len(token),
            type(provider).__name__,
        )
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
        cls._entries_since_last_anchor = 0
        cls._last_anchor_stamp = entry.stamp

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

        Auto-initializes the chain if empty.  Automatically inserts a
        re-anchor entry every ``REANCHOR_INTERVAL`` normal entries
        (if configured).
        """
        last_seq, last_hash = cls._get_last()

        if last_seq == 0:
            cls.initialize()

        # Re-read after possible initialization
        last_seq, last_hash = cls._get_last()

        entry = cls._create_entry(last_hash, last_seq + 1, data)
        cls._entries_since_last_anchor += 1

        logger.debug('ImmutableLog append: #%d hash=%s', entry.sequence, entry.entry_hash.hex()[:16])

        # Check if we should insert a re-anchor
        if cls._should_reanchor():
            cls._append_reanchor(entry.entry_hash)

        return entry

    @classmethod
    def append_object(cls, obj: typing.Any) -> ImmutableLog:
        """Append a log entry, serializing ``obj`` via pickle."""
        return cls.append(content_to_bytes(obj))

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

        if genesis.previous_hash != expected_seed:
            return False, (
                f'Genesis seed mismatch: '
                f'stored={genesis.previous_hash.hex()} '
                f'expected={expected_seed.hex()} '
                f'(nonce={nonce.hex()[:16]}...)'
            )

        # Verify genesis TSA token
        if genesis_provider is not None:
            if not genesis_provider.verify(expected_seed, token):
                return False, 'Genesis RFC 3161 token verification failed.'

        # Walk the chain
        expected_previous = genesis.entry_hash
        reanchor_count = 0

        for entry in entries[1:]:
            computed = cls._compute_hash(
                entry.previous_hash,
                entry.stamp,
                entry.sequence,
                entry.data,
            )

            if computed != entry.entry_hash:
                return False, (
                    f'Hash mismatch at entry #{entry.sequence}: '
                    f'stored={entry.entry_hash.hex()} computed={computed.hex()}'
                )

            if entry.previous_hash != expected_previous:
                return False, (
                    f'Chain break at entry #{entry.sequence}: '
                    f'expected prev={expected_previous.hex()} '
                    f'got={entry.previous_hash.hex()}'
                )

            # Re-anchor verification
            if entry.anchor:
                if reanchor_provider is not None:
                    if not reanchor_provider.verify(expected_previous, entry.data):
                        return False, (
                            f'Re-anchor TSA verification failed at entry #{entry.sequence}'
                        )
                reanchor_count += 1
                logger.debug('Re-anchor #%d verified OK', entry.sequence)

            expected_previous = entry.entry_hash

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
            total_entries, anchor_count, normal_count,
            entries_since_last_anchor, last_anchor_stamp, reanchor_config.
        """
        total = ImmutableLog.objects.count()
        anchor_count = ImmutableLog.objects.filter(anchor=True).count()

        return {
            'total_entries': total,
            'anchor_count': anchor_count,
            'normal_count': total - anchor_count,
            'entries_since_last_anchor': cls._entries_since_last_anchor,
            'last_anchor_stamp': cls._last_anchor_stamp,
            'reanchor_config': str(cls.REANCHOR) if cls.REANCHOR else 'disabled',
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

        if first.previous_hash != expected_seed:
            logger.error('Genesis seed mismatch')
            return

        if genesis_provider and not genesis_provider.verify(expected_seed, token):
            logger.error('Genesis token verification failed')
            return

        yield first
        expected_previous = first.entry_hash

        for entry in entries[1:]:
            if entry.previous_hash != expected_previous:
                logger.error('Chain break at entry #%d', entry.sequence)
                return

            computed = cls._compute_hash(
                entry.previous_hash, entry.stamp, entry.sequence, entry.data
            )
            if computed != entry.entry_hash:
                logger.error('Hash mismatch at entry #%d', entry.sequence)
                return

            # Verify re-anchor
            if entry.anchor:
                if reanchor_provider and not reanchor_provider.verify(
                    expected_previous, entry.data
                ):
                    logger.error(
                        'Re-anchor TSA verification failed at entry #%d', entry.sequence
                    )
                    return

            expected_previous = entry.entry_hash
            yield entry
