"""GPU memory gate — serializes GPU-heavy operations and queues vision behind chat."""
from __future__ import annotations

import asyncio
import subprocess
import time

import structlog

_LOGGER = structlog.get_logger()
_CACHE_TTL = 2.0  # seconds between nvidia-smi calls


class GPUMemoryGate:
    def __init__(self, min_free_mb: int = 512) -> None:
        self._min_free_mb = min_free_mb
        self._sem = asyncio.Semaphore(1)  # serialize GPU-heavy ops
        self._chat_lock = asyncio.Lock()  # held while Ollama chat is active
        self._last_check: float = 0
        self._last_free_mb: int = 9999

    def _query_free_mb(self) -> int:
        now = time.monotonic()
        if now - self._last_check < _CACHE_TTL:
            return self._last_free_mb
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                timeout=3, text=True,
            ).strip()
            self._last_free_mb = int(out.split(",")[0].strip())
        except Exception:
            self._last_free_mb = 9999  # assume OK if nvidia-smi fails
        self._last_check = now
        return self._last_free_mb

    async def acquire(self, caller: str = "") -> bool:
        """Acquire the GPU gate. Checks VRAM and skips if chat is active."""
        # If chat is active, wait briefly but don't block vision forever
        if self._chat_lock.locked():
            try:
                await asyncio.wait_for(self._chat_lock.acquire(), timeout=5.0)
                self._chat_lock.release()
            except asyncio.TimeoutError:
                _LOGGER.debug("gpu_gate.chat_active_proceeding", caller=caller)
                # Proceed anyway — better a slow vision call than no vision at all

        await self._sem.acquire()
        free = await asyncio.get_event_loop().run_in_executor(None, self._query_free_mb)
        if free < self._min_free_mb:
            self._sem.release()
            _LOGGER.warning("gpu_gate.insufficient_memory",
                            free_mb=free, min_mb=self._min_free_mb, caller=caller)
            return False
        return True

    def release(self) -> None:
        self._sem.release()

    def chat_started(self) -> None:
        """Call when an Ollama chat request starts."""
        if not self._chat_lock.locked():
            # Use non-blocking acquire from sync context
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._acquire_chat_lock())
            except RuntimeError:
                pass

    async def _acquire_chat_lock(self) -> None:
        await self._chat_lock.acquire()

    def chat_finished(self) -> None:
        """Call when an Ollama chat request completes."""
        if self._chat_lock.locked():
            try:
                self._chat_lock.release()
            except RuntimeError:
                pass

    @property
    def free_mb(self) -> int:
        return self._query_free_mb()
