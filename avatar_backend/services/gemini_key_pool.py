"""
GeminiKeyPool — round-robin Gemini API key rotation with 429 cooldown.

Distributes vision API calls across multiple free-tier Gemini API keys.
When a key hits 429 (rate limit), it enters cooldown and the next key is used.
Optionally pin specific cameras to specific keys for guaranteed quota.
"""
from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field

import structlog

_LOGGER = structlog.get_logger()
_DEFAULT_COOLDOWN_S = 60


@dataclass
class KeyState:
    key: str
    label: str = ""
    cooldown_until: float = 0.0
    total_calls: int = 0
    total_429s: int = 0
    last_used: float = 0.0
    pinned_cameras: list[str] = field(default_factory=list)

    @property
    def is_available(self) -> bool:
        return time.monotonic() > self.cooldown_until

    @property
    def masked_key(self) -> str:
        if len(self.key) <= 8:
            return "****"
        return self.key[:4] + "…" + self.key[-4:]

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "masked_key": self.masked_key,
            "available": self.is_available,
            "cooldown_remaining_s": max(0, round(self.cooldown_until - time.monotonic())),
            "total_calls": self.total_calls,
            "total_429s": self.total_429s,
            "last_used": self.last_used,
            "pinned_cameras": self.pinned_cameras,
        }


class GeminiKeyPool:
    """Round-robin Gemini API key pool with 429 cooldown and camera pinning."""

    def __init__(self, cooldown_s: float = _DEFAULT_COOLDOWN_S) -> None:
        self._keys: list[KeyState] = []
        self._lock = threading.Lock()
        self._robin_idx = 0
        self._cooldown_s = cooldown_s

    @property
    def size(self) -> int:
        return len(self._keys)

    @property
    def available_count(self) -> int:
        with self._lock:
            return sum(1 for k in self._keys if k.is_available)

    def add_key(self, key: str, label: str = "") -> None:
        """Add a key to the pool. Deduplicates by key value."""
        key = key.strip()
        if not key:
            return
        with self._lock:
            if any(k.key == key for k in self._keys):
                return
            idx = len(self._keys)
            self._keys.append(KeyState(key=key, label=label or f"Key {idx + 1}"))
        _LOGGER.info("gemini_pool.key_added", label=label or f"Key {idx + 1}", pool_size=len(self._keys))

    def remove_key(self, index: int) -> bool:
        """Remove a key by index."""
        with self._lock:
            if 0 <= index < len(self._keys):
                removed = self._keys.pop(index)
                if self._robin_idx >= len(self._keys):
                    self._robin_idx = 0
                _LOGGER.info("gemini_pool.key_removed", label=removed.label)
                return True
        return False

    def pin_camera(self, key_index: int, camera_id: str) -> None:
        """Pin a camera to a specific key."""
        with self._lock:
            # Remove camera from any existing pin
            for k in self._keys:
                if camera_id in k.pinned_cameras:
                    k.pinned_cameras.remove(camera_id)
            if 0 <= key_index < len(self._keys):
                self._keys[key_index].pinned_cameras.append(camera_id)

    def unpin_camera(self, camera_id: str) -> None:
        """Remove camera pin."""
        with self._lock:
            for k in self._keys:
                if camera_id in k.pinned_cameras:
                    k.pinned_cameras.remove(camera_id)

    def get_key(self, camera_id: str | None = None) -> str | None:
        """Get the next available API key. Returns None if all exhausted.

        If camera_id is pinned to a key and that key is available, use it.
        Otherwise round-robin across available keys.
        """
        with self._lock:
            if not self._keys:
                return None

            # Check camera pin first
            if camera_id:
                for k in self._keys:
                    if camera_id in k.pinned_cameras and k.is_available:
                        k.total_calls += 1
                        k.last_used = time.monotonic()
                        return k.key

            # Round-robin across available keys
            n = len(self._keys)
            for _ in range(n):
                k = self._keys[self._robin_idx]
                self._robin_idx = (self._robin_idx + 1) % n
                if k.is_available:
                    k.total_calls += 1
                    k.last_used = time.monotonic()
                    return k.key

        return None

    def report_429(self, key: str) -> None:
        """Mark a key as rate-limited. Enters cooldown."""
        with self._lock:
            for k in self._keys:
                if k.key == key:
                    k.cooldown_until = time.monotonic() + self._cooldown_s
                    k.total_429s += 1
                    _LOGGER.warning("gemini_pool.key_rate_limited",
                                    label=k.label, cooldown_s=self._cooldown_s,
                                    available=self.available_count)
                    return

    def report_success(self, key: str) -> None:
        """Mark a successful call (clears any residual state)."""
        pass  # No action needed — cooldown expires naturally

    def get_status(self) -> list[dict]:
        """Return status of all keys for the admin UI."""
        with self._lock:
            return [k.to_dict() for k in self._keys]

    def get_stats(self) -> dict:
        """Return pool-level stats."""
        with self._lock:
            return {
                "pool_size": len(self._keys),
                "available": sum(1 for k in self._keys if k.is_available),
                "total_calls": sum(k.total_calls for k in self._keys),
                "total_429s": sum(k.total_429s for k in self._keys),
            }
