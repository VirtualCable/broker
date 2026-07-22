# -*- coding: utf-8 -*-
#
# Copyright (c) 2022 Virtual Cable S.L.
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
#    * Neither the name of Virtual Cable S.L. nor the names of its contributors
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
"""

"""
Exponential backoff cache for per-key "bad host / bad resource" tracking.

Used to avoid hammering a known-broken resource (LDAP DC, remote service,
unhealthy transport, ...) by spacing retries exponentially until the
resource recovers.

Design
------
Each instance owns a namespace (``owner``) inside the shared cache. Within
that namespace it manages two keys per ``key``:

- ``{owner}.bad.{key}``         -> bool flag, "is this key currently bad?"
- ``{owner}.cooldown.{key}``    -> int seconds, current backoff value

A ``mark_bad`` doubles the cooldown (multiplied by ``scale``) up to
``max_time``. The next ``mark_bad`` after the cooldown's TTL has elapsed
will read the *stale* cooldown entry and double from there, so a resource
that stays broken for hours eventually tops out at ``max_time``.

A successful operation must call ``clear_bad`` to wipe both entries and
restart from the seed. ``cache.get`` never raises, so any stale entry that
outlives the flag is harmless: it is consumed by the next ``mark_bad``.
"""
import logging
import typing

from uds.core.util.cache import Cache, CacheLike

logger = logging.getLogger(__name__)


class Backoff:
    """
    Per-key exponential backoff stored in a shared ``CacheLike``.

    The cache is expected to provide ``get`` / ``put`` / ``remove`` and to
    never raise from ``get`` (it must return ``default`` on miss or TTL
    expiry). ``put`` may raise (e.g. DB transaction in progress) — those
    errors are swallowed and logged at debug level, since a missed cache
    write only costs us an extra retry attempt.

    Args:
        cache:     the cache backend. If ``None`` (default), a process-wide
                   ``Cache('backoff')`` is created lazily, so backoff state
                   is shared across all callers of ``Backoff`` in the same
                   UDS process. This is the right default when the badness
                   of a resource is global (a network endpoint that fails
                   once will fail for every caller). If the resource is
                   per-caller, pass a caller-specific cache.
        owner:     namespace for the keys this instance manages. Distinct
                   owners (e.g. ``"ldap"``, ``"osp.router"``) coexist in the
                   same cache without colliding.
        fail_time: initial backoff in seconds on the first failure.
        max_time:  hard cap on the backoff in seconds.
        scale:     multiplier applied to the previous backoff on each
                   consecutive failure (must be ``>= 1``).
    """

    _cache: CacheLike
    _owner: str
    _fail_time: int
    _max_time: int
    _scale: float
    # Per-entry TTL is the current cooldown *this* — but we cap it
    # generously so a stale entry survives long enough to be consumed
    # by the next mark_bad after a real outage.
    _max_ttl: int

    def __init__(
        self,
        cache: CacheLike | None = None,
        *,
        owner: str,
        fail_time: int = 30,
        max_time: int = 28800,
        scale: float = 2.0,
    ) -> None:
        if not owner:
            raise ValueError("owner must be a non-empty namespace")
        if fail_time <= 0:
            raise ValueError("fail_time must be > 0")
        if max_time < fail_time:
            raise ValueError("max_time must be >= fail_time")
        if scale < 1.0:
            raise ValueError("scale must be >= 1.0")
        # Shared process-wide cache when none is provided. ``Cache`` itself
        # is a thin wrapper over the DB-backed ``Cache`` model and is safe
        # to instantiate multiple times (each instance has its own owner).
        self._cache = cache if cache is not None else Cache("backoff")
        self._owner = owner
        self._fail_time = fail_time
        self._max_time = max_time
        self._scale = scale
        # Per-entry TTL is the current cooldown *this* — but we cap it
        # generously so a stale entry survives long enough to be consumed
        # by the next mark_bad after a real outage.
        self._max_ttl = max_time * 4

    def _bad_key(self, key: str) -> str:
        return f"{self._owner}.bad.{key}"

    def _cooldown_key(self, key: str) -> str:
        return f"{self._owner}.cooldown.{key}"

    def is_bad(self, key: str) -> bool:
        """``True`` if the resource identified by ``key`` is in cooldown."""
        return bool(self._cache.get(self._bad_key(key), default=None))

    def ttl(self, key: str) -> int:
        """Current backoff (seconds) for ``key``; ``0`` if not bad."""
        try:
            return self._cache.get(self._cooldown_key(key), default=None) or 0
        except (TypeError, ValueError):
            return 0

    def mark_bad(self, key: str) -> int:
        """
        Register a failure for ``key`` and return the new backoff in seconds.

        The first failure uses ``fail_time`` as the seed. Each subsequent
        failure (even after the previous TTL has expired) reads the stale
        cooldown, multiplies it by ``scale``, and caps at ``max_time``.
        """
        current = self._cache.get(self._cooldown_key(key), default=None)
        try:
            prev = int(current) if current is not None else 0
        except (TypeError, ValueError):
            prev = 0
        next_value = self._fail_time if prev == 0 else int(prev * self._scale)
        next_value = min(next_value, self._max_time)
        ttl = min(next_value, self._max_ttl)
        self._safe_put(self._cooldown_key(key), next_value, ttl)
        self._safe_put(self._bad_key(key), True, ttl)
        return next_value

    def clear_bad(self, key: str, *, force: bool = False) -> None:
        """
        Clear the backoff state for ``key``.

        ``force`` is provided for symmetry with future policies (e.g. caller
        wants to wipe even an entry that "looks fine"). Today it is
        functionally identical to a normal clear, since the only state
        stored is the two keys we always remove.
        """
        del force  # currently a no-op; kept for API stability
        self._cache.remove(self._bad_key(key))
        self._cache.remove(self._cooldown_key(key))

    def _safe_put(self, key: str, value: typing.Any, ttl: int) -> None:
        try:
            self._cache.put(key, value, validity=ttl)
        except Exception:
            logger.debug("Cache.put raised in Backoff; ignoring")
