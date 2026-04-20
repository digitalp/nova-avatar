"""
TTS fallback chain — wraps a primary BaseTTSService with ordered fallbacks.

On primary failure, each fallback is tried in order with a 10-second timeout.
If all providers fail, a 100 ms silent WAV is returned so the voice pipeline
never raises an unhandled exception.
"""
from __future__ import annotations

import asyncio

import structlog

from avatar_backend.services.tts_service import (
    BaseTTSService,
    PiperTTSService,
    _silent_wav,
)

_LOGGER = structlog.get_logger()

_FALLBACK_TIMEOUT_S = 20


class FallbackTTSService(BaseTTSService):
    """Wraps a primary TTS provider with an ordered list of fallbacks."""

    def __init__(
        self,
        primary: BaseTTSService,
        fallbacks: list[BaseTTSService],
    ) -> None:
        self._primary = primary
        # Skip duplicate Piper in fallback chain if primary is already Piper
        self._fallbacks = [
            fb for fb in fallbacks
            if not (isinstance(self._primary, PiperTTSService) and isinstance(fb, PiperTTSService))
        ]

    async def synthesise(self, text: str) -> bytes:
        result = await self._try_chain(text, with_timing=False)
        assert isinstance(result, bytes)
        return result

    async def synthesise_with_timing(self, text: str) -> tuple[bytes, list[dict]]:
        result = await self._try_chain(text, with_timing=True)
        assert isinstance(result, tuple)
        return result

    @property
    def is_ready(self) -> bool:
        if self._primary.is_ready:
            return True
        return any(fb.is_ready for fb in self._fallbacks)

    # ── internal ──────────────────────────────────────────────────────

    async def _try_chain(
        self, text: str, *, with_timing: bool
    ) -> bytes | tuple[bytes, list[dict]]:
        providers = [self._primary, *self._fallbacks]
        last_exc: BaseException | None = None

        for idx, provider in enumerate(providers):
            try:
                if with_timing:
                    result = await asyncio.wait_for(
                        provider.synthesise_with_timing(text),
                        timeout=_FALLBACK_TIMEOUT_S,
                    )
                else:
                    result = await asyncio.wait_for(
                        provider.synthesise(text),
                        timeout=_FALLBACK_TIMEOUT_S,
                    )
                # If this wasn't the primary, log the fallback event
                if idx > 0:
                    _LOGGER.warning(
                        "tts.fallback_used",
                        failed_provider=type(providers[idx - 1]).__name__,
                        fallback_provider=type(provider).__name__,
                        error=str(last_exc),
                    )
                return result
            except Exception as exc:
                last_exc = exc

        # All providers failed — return silent WAV
        _LOGGER.error(
            "tts.all_providers_failed",
            providers=[type(p).__name__ for p in providers],
            error=str(last_exc),
        )
        silent = _silent_wav()
        if with_timing:
            return silent, []
        return silent
