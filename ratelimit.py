"""In-process rate limiter — fixed-window, per-key.

Single-process for now. Render's free tier doesn't auto-scale, so a
per-process counter is sufficient. Move to Redis when traffic justifies
multi-instance — public surface (allow) is unchanged, only the storage
backend swaps.
"""

from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()
_BUCKETS: dict[str, tuple[int, float]] = {}  # key → (count, window_start)


def allow(key: str, limit: int, window_sec: int) -> bool:
    """Returns True if the action is allowed; False if rate-limited.
    Window resets when the current window's age >= window_sec."""
    now = time.time()
    with _LOCK:
        count, window_start = _BUCKETS.get(key, (0, now))
        if now - window_start >= window_sec:
            count = 0
            window_start = now
        if count >= limit:
            return False
        _BUCKETS[key] = (count + 1, window_start)
    return True
