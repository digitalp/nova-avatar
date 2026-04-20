"""
Property-based tests for FallbackTTSService.

# Feature: nova-v1-improvements, Property 4: TTS fallback chain round-trip safety
#
# For any valid text input, calling synthesise() on the FallbackTTSService
# SHALL return non-empty bytes (either speech audio or a valid silent WAV)
# without raising an unhandled exception, regardless of which providers in
# the chain fail.
#
# Validates: Requirements 2.2, 2.4, 2.7
"""
from __future__ import annotations

import asyncio
import io
import wave

import pytest
from hypothesis import given, settings, strategies as st

from avatar_backend.services.tts_service import BaseTTSService, _silent_wav
from avatar_backend.services.tts_fallback import FallbackTTSService


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_wav(tag: bytes = b"\x01\x00") -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(tag * 100)
    return buf.getvalue()


def _is_valid_wav(data: bytes) -> bool:
    """Return True if data is a parseable WAV with at least one frame."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            return wf.getnframes() > 0
    except Exception:
        return False


class _ConfigurableTTS(BaseTTSService):
    """Stub whose behaviour is controlled per-call."""

    def __init__(self, *, fail: bool = False, audio: bytes | None = None):
        self._fail = fail
        self._audio = audio or _make_wav()

    async def synthesise(self, text: str) -> bytes:
        if self._fail:
            raise RuntimeError("provider down")
        return self._audio

    async def synthesise_with_timing(self, text: str) -> tuple[bytes, list[dict]]:
        if self._fail:
            raise RuntimeError("provider down")
        return self._audio, [{"word": "w", "start_ms": 0, "end_ms": 50}]

    @property
    def is_ready(self) -> bool:
        return not self._fail


# Strategy: generate a list of booleans representing which providers fail.
# True = provider fails, False = provider succeeds.
# At least one provider (primary) is always present.
_fail_pattern = st.lists(st.booleans(), min_size=1, max_size=5)

# Strategy: arbitrary non-empty text input
_text_input = st.text(min_size=1, max_size=200)


# ── Property tests ────────────────────────────────────────────────────────────

@given(text=_text_input, failures=_fail_pattern)
@settings(max_examples=200, deadline=5000)
@pytest.mark.asyncio
async def test_synthesise_always_returns_nonempty_valid_wav(text: str, failures: list[bool]):
    """
    Property 4 (synthesise path): For ANY text and ANY combination of
    provider failures, synthesise() returns non-empty bytes that form a
    valid WAV — never raises an unhandled exception.
    """
    providers = [_ConfigurableTTS(fail=f) for f in failures]
    primary, fallbacks = providers[0], providers[1:]
    svc = FallbackTTSService(primary, fallbacks)

    result = await svc.synthesise(text)

    assert isinstance(result, bytes)
    assert len(result) > 0
    assert _is_valid_wav(result)


@given(text=_text_input, failures=_fail_pattern)
@settings(max_examples=200, deadline=5000)
@pytest.mark.asyncio
async def test_synthesise_with_timing_always_returns_nonempty_wav_and_list(
    text: str, failures: list[bool]
):
    """
    Property 4 (synthesise_with_timing path): For ANY text and ANY
    combination of provider failures, synthesise_with_timing() returns
    (non-empty WAV bytes, list) — never raises an unhandled exception.
    """
    providers = [_ConfigurableTTS(fail=f) for f in failures]
    primary, fallbacks = providers[0], providers[1:]
    svc = FallbackTTSService(primary, fallbacks)

    wav, timings = await svc.synthesise_with_timing(text)

    assert isinstance(wav, bytes)
    assert len(wav) > 0
    assert _is_valid_wav(wav)
    assert isinstance(timings, list)


@given(text=_text_input)
@settings(max_examples=100, deadline=5000)
@pytest.mark.asyncio
async def test_all_providers_fail_still_returns_silent_wav(text: str):
    """
    Property 4 (total failure): When every provider in the chain fails,
    the result is still a valid non-empty WAV (the silent fallback).
    """
    primary = _ConfigurableTTS(fail=True)
    fallbacks = [_ConfigurableTTS(fail=True), _ConfigurableTTS(fail=True)]
    svc = FallbackTTSService(primary, fallbacks)

    result = await svc.synthesise(text)

    assert isinstance(result, bytes)
    assert len(result) > 0
    assert _is_valid_wav(result)

    wav_with_timing, timings = await svc.synthesise_with_timing(text)
    assert _is_valid_wav(wav_with_timing)
    assert timings == []
