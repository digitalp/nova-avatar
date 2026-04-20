"""Per-session sliding-window rate limiter for chat and voice endpoints."""
from __future__ import annotations

import threading
import time
from collections import defaultdict


class SessionRateLimiter:
    def __init__(self, max_requests: int = 30, window_s: int = 60) -> None:
        self._max = max_requests
        self._window = window_s
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, session_id: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds). Falls back to key as-is."""
        key = (session_id or "").strip() or "__no_session__"
        now = time.monotonic()
        with self._lock:
            window = self._hits[key] = [t for t in self._hits[key] if now - t < self._window]
            if len(window) >= self._max:
                oldest = window[0]
                retry_after = int(self._window - (now - oldest)) + 1
                return False, max(retry_after, 1)
            window.append(now)
            return True, 0

    def cleanup(self) -> None:
        now = time.monotonic()
        with self._lock:
            expired = [k for k, v in self._hits.items() if all(now - t >= self._window for t in v)]
            for k in expired:
                del self._hits[k]

    def update_config(self, max_requests: int, window_s: int) -> None:
        self._max = max_requests
        self._window = window_s
