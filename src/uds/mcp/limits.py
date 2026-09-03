"""In-process rate limiting for MCP requests.

Keeps a sliding one-minute window per caller key in process memory.
Each worker applies its own window, so the effective limit of a
multi-worker deployment is multiplied by the number of workers: this is
an abuse guard, not a strict global quota. The limit value comes from
``GlobalConfig.MCP_RATE_LIMIT`` and is read per request by the caller.
"""

import collections
import threading
import time

_WINDOW_SECONDS: float = 60.0
_MAX_BUCKETS: int = 10_000  # crude memory guard

_BUCKETS: dict[str, collections.deque[float]] = {}
_LOCK = threading.Lock()


def allow_request(key: str, limit_per_minute: int) -> bool:
    """Register a request for ``key`` and return whether it fits the window.

    ``limit_per_minute`` of zero (or less) disables the limit entirely.
    """
    if limit_per_minute <= 0:
        return True

    now = time.monotonic()
    with _LOCK:
        if len(_BUCKETS) > _MAX_BUCKETS:
            _prune(now)
        bucket = _BUCKETS.setdefault(key, collections.deque())
        cutoff = now - _WINDOW_SECONDS
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit_per_minute:
            return False
        bucket.append(now)
        return True


def _prune(now: float) -> None:
    """Drop stale buckets so the key map does not grow without bound."""
    cutoff = now - _WINDOW_SECONDS
    stale = [key for key, bucket in _BUCKETS.items() if not bucket or bucket[-1] <= cutoff]
    for key in stale:
        del _BUCKETS[key]
