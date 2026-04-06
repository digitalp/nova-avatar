"""
DecisionLog — lightweight in-memory ring buffer for Nova AI decision events.

Events are broadcast to all active SSE subscribers (admin panel live log).
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Any

_MAX_ENTRIES = 300   # keep last 300 decisions in memory


class DecisionLog:
    def __init__(self) -> None:
        self._entries: list[dict] = []
        self._subscribers: list[asyncio.Queue] = []

    # ── Public API ────────────────────────────────────────────────────────

    def record(self, kind: str, **fields: Any) -> dict:
        """Append a decision event and fan it out to all SSE subscribers."""
        entry: dict = {
            "ts":   datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "kind": kind,
            **fields,
        }
        self._entries.append(entry)
        if len(self._entries) > _MAX_ENTRIES:
            self._entries.pop(0)
        dead: list[asyncio.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.remove(q)
        return entry

    def recent(self, n: int = 200) -> list[dict]:
        return list(self._entries[-n:])

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass
