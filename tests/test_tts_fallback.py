"""
Unit tests for FallbackTTSService.
"""
import asyncio
import io
import wave

import pytest

from avatar_backend.services.tts_service import BaseTTSService, PiperTTSService, _silent_wav
from avatar_backend.services.tts_fallback import FallbackTTSService


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_wav(tag: bytes = b"\x01\x00") -> bytes:
    """Return a minimal valid WAV with identifiable PCM content."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(tag * 100)
    return buf.getvalue()


class StubTTS(BaseTTSService):
    """Controllable stub: returns preset audio or raises."""

    def __init__(self, *, audio: bytes | None = None, fail: bool = False, ready: bool = True):
        self._audio = audio or _make_wav()
        self._fail = fail
        self._ready = ready

    async def synthesise(self, text: str) -> bytes:
        if self._fail:
            raise RuntimeError("StubTTS failure")
        return self._audio

    async def synthesise_with_timing(self, text: str) -> tuple[bytes, list[dict]]:
        if self._fail:
            raise RuntimeError("StubTTS failure")
        return self._audio, [{"word": "hello", "start_ms": 0, "end_ms": 100}]

    @property
    def is_ready(self) -> bool:
        return self._ready


class SlowTTS(BaseTTSService):
    """Stub that sleeps longer than the fallback timeout."""

    async def synthesise(self, text: str) -> bytes:
        await asyncio.sleep(30)
        return _make_wav()

    async def synthesise_with_timing(self, text: str) -> tuple[bytes, list[dict]]:
        await asyncio.sleep(30)
        return _make_wav(), []

    @property
    def is_ready(self) -> bool:
        return True


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_primary_success_returns_primary_audio():
    primary_audio = _make_wav(b"\xAA\x00")
    primary = StubTTS(audio=primary_audio)
    fallback = StubTTS(audio=_make_wav(b"\xBB\x00"))
    svc = FallbackTTSService(primary, [fallback])

    result = await svc.synthesise("hello")
    assert result == primary_audio


@pytest.mark.asyncio
async def test_primary_failure_uses_fallback():
    primary = StubTTS(fail=True)
    fallback_audio = _make_wav(b"\xCC\x00")
    fallback = StubTTS(audio=fallback_audio)
    svc = FallbackTTSService(primary, [fallback])

    result = await svc.synthesise("hello")
    assert result == fallback_audio


@pytest.mark.asyncio
async def test_all_failures_return_silent_wav():
    primary = StubTTS(fail=True)
    fallback = StubTTS(fail=True)
    svc = FallbackTTSService(primary, [fallback])

    result = await svc.synthesise("hello")
    # Should be a valid WAV (silent)
    with wave.open(io.BytesIO(result), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2


@pytest.mark.asyncio
async def test_synthesise_with_timing_fallback():
    primary = StubTTS(fail=True)
    fallback_audio = _make_wav(b"\xDD\x00")
    fallback = StubTTS(audio=fallback_audio)
    svc = FallbackTTSService(primary, [fallback])

    wav, timings = await svc.synthesise_with_timing("hello")
    assert wav == fallback_audio
    assert len(timings) > 0


@pytest.mark.asyncio
async def test_synthesise_with_timing_all_fail_returns_silent():
    primary = StubTTS(fail=True)
    fallback = StubTTS(fail=True)
    svc = FallbackTTSService(primary, [fallback])

    wav, timings = await svc.synthesise_with_timing("hello")
    assert wav[:4] == b"RIFF"
    assert timings == []


@pytest.mark.asyncio
async def test_timeout_triggers_fallback():
    """A slow primary should time out and fall through to the fallback."""
    primary = SlowTTS()
    fallback_audio = _make_wav(b"\xEE\x00")
    fallback = StubTTS(audio=fallback_audio)
    svc = FallbackTTSService(primary, [fallback])

    # Patch the timeout to something short for the test
    import avatar_backend.services.tts_fallback as mod
    original = mod._FALLBACK_TIMEOUT_S
    mod._FALLBACK_TIMEOUT_S = 0.1
    try:
        result = await svc.synthesise("hello")
        assert result == fallback_audio
    finally:
        mod._FALLBACK_TIMEOUT_S = original


def test_skip_duplicate_piper_in_chain():
    """If primary is Piper, a Piper fallback should be filtered out."""
    primary = PiperTTSService()
    piper_fallback = PiperTTSService()
    other_fallback = StubTTS()
    svc = FallbackTTSService(primary, [piper_fallback, other_fallback])

    assert len(svc._fallbacks) == 1
    assert isinstance(svc._fallbacks[0], StubTTS)


def test_piper_not_skipped_when_primary_is_not_piper():
    """If primary is NOT Piper, Piper fallback should remain."""
    primary = StubTTS()
    piper_fallback = PiperTTSService()
    svc = FallbackTTSService(primary, [piper_fallback])

    assert len(svc._fallbacks) == 1
    assert isinstance(svc._fallbacks[0], PiperTTSService)


def test_is_ready_true_when_primary_ready():
    primary = StubTTS(ready=True)
    fallback = StubTTS(ready=False)
    svc = FallbackTTSService(primary, [fallback])
    assert svc.is_ready is True


def test_is_ready_true_when_only_fallback_ready():
    primary = StubTTS(ready=False)
    fallback = StubTTS(ready=True)
    svc = FallbackTTSService(primary, [fallback])
    assert svc.is_ready is True


def test_is_ready_false_when_none_ready():
    primary = StubTTS(ready=False)
    fallback = StubTTS(ready=False)
    svc = FallbackTTSService(primary, [fallback])
    assert svc.is_ready is False
