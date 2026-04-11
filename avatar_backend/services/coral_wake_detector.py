"""
CoralWakeDetector — 3-stage wake word detection pipeline.

Stage 1 — Coral Edge TPU TFLite (if nova_wakeword_edgetpu.tflite is present)
           ~1ms on device. Fires when a trained "Nova" keyword model is available.
           Train via the admin panel → Settings → Train Wake Word Model.

Stage 2 — openWakeWord Silero VAD (CPU, ~14ms per second of audio)
           Detects whether the audio chunk contains speech at all.
           Eliminates ~90% of Whisper calls (silent/ambient audio is discarded).

Stage 3 — Whisper transcribe_wake (GPU/CPU, ~150-400ms)
           Only called when VAD confirms actual speech.
           Checks transcript against _WAKE_VARIANTS ("nova", "noah", etc.).
           This is the existing Whisper path, now used as a targeted fallback.

Result includes 'method' field: "coral" | "whisper_after_vad" | "whisper_direct"
so you can see in logs which stage fired the wake event.
"""
from __future__ import annotations

import asyncio
import io
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Awaitable

import numpy as np
import structlog

_LOGGER = structlog.get_logger()

_EDGETPU_LIB   = "/usr/lib/x86_64-linux-gnu/libedgetpu.so.1"
_CORAL_MODEL   = Path(__file__).parent.parent.parent / "models" / "coral" / "nova_wakeword_edgetpu.tflite"

# VAD threshold — audio is passed to Whisper only if any 30ms frame exceeds this
_VAD_THRESHOLD = 0.4
# Coral confidence — scores above this are treated as a confirmed wake
_CORAL_THRESHOLD = 0.85
# Frame size expected by Silero VAD (30ms at 16 kHz)
_VAD_FRAME_SIZE = 480
_SAMPLE_RATE    = 16000


@dataclass
class WakeResult:
    wake:        bool
    transcript:  str   = ""
    method:      str   = ""   # coral | whisper_after_vad | whisper_direct | vad_silence
    score:       float = 0.0
    elapsed_ms:  float = 0.0


class CoralWakeDetector:
    """
    Drop-in replacement for the raw Whisper /stt/wake path.

    Usage:
        detector = CoralWakeDetector.build(stt_service)
        result = await detector.detect(raw_audio_bytes)
    """

    def __init__(
        self,
        stt,                            # STTService with .transcribe_wake()
        is_wake_word_fn: Callable[[str], bool],
        coral_interp=None,              # ai_edge_litert Interpreter (optional)
        vad=None,                       # openwakeword VAD (optional)
        executor=None,
    ) -> None:
        self._stt            = stt
        self._is_wake_word   = is_wake_word_fn
        self._coral          = coral_interp
        self._vad            = vad
        self._executor       = executor

        if coral_interp is not None:
            _LOGGER.info("coral_wake.coral_active",
                         detail="Coral Edge TPU wake word model loaded")
        if vad is not None:
            _LOGGER.info("coral_wake.vad_active",
                         detail="Silero VAD gate active — Whisper skipped on silent chunks")

    @classmethod
    def build(cls, stt, is_wake_word_fn: Callable[[str], bool]) -> "CoralWakeDetector":
        coral_interp = cls._try_load_coral()
        vad          = cls._try_load_vad()
        return cls(stt, is_wake_word_fn, coral_interp=coral_interp, vad=vad)

    # ── Stage 1: Coral ────────────────────────────────────────────────────────

    @staticmethod
    def _try_load_coral():
        if not _CORAL_MODEL.exists():
            _LOGGER.info(
                "coral_wake.no_model",
                path=str(_CORAL_MODEL),
                detail="Coral wake word skipped — train a model via admin Settings",
            )
            return None
        try:
            from ai_edge_litert.interpreter import Interpreter, load_delegate
            delegate = load_delegate(_EDGETPU_LIB)
            interp   = Interpreter(model_path=str(_CORAL_MODEL),
                                   experimental_delegates=[delegate])
            interp.allocate_tensors()
            _LOGGER.info("coral_wake.coral_ready", model=str(_CORAL_MODEL))
            return interp
        except Exception as exc:
            _LOGGER.warning("coral_wake.coral_load_failed", exc=str(exc))
            return None

    # ── Stage 2: Silero VAD ───────────────────────────────────────────────────

    @staticmethod
    def _try_load_vad():
        try:
            from openwakeword.vad import VAD
            vad = VAD()
            # Warm up
            silence = np.zeros(_VAD_FRAME_SIZE, dtype=np.float32)
            vad.predict(silence)
            _LOGGER.info("coral_wake.vad_ready", frame_ms=30, threshold=_VAD_THRESHOLD)
            return vad
        except Exception as exc:
            _LOGGER.warning("coral_wake.vad_load_failed", exc=str(exc),
                            detail="Whisper will run on all audio chunks")
            return None

    # ── Public API ────────────────────────────────────────────────────────────

    async def detect(self, audio_bytes: bytes) -> WakeResult:
        t0 = time.perf_counter()

        # Stage 1: Coral TFLite keyword model
        if self._coral is not None:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                self._executor, self._run_coral, audio_bytes
            )
            if result is not None:
                result.elapsed_ms = (time.perf_counter() - t0) * 1000
                _LOGGER.info(
                    "coral_wake.result",
                    wake=result.wake, method=result.method,
                    score=round(result.score, 3),
                    elapsed_ms=round(result.elapsed_ms, 1),
                )
                return result

        # Stage 2: VAD gate — skip Whisper on silent chunks
        if self._vad is not None:
            loop = asyncio.get_running_loop()
            has_speech = await loop.run_in_executor(
                self._executor, self._run_vad, audio_bytes
            )
            if not has_speech:
                elapsed = (time.perf_counter() - t0) * 1000
                _LOGGER.debug("coral_wake.vad_silence", elapsed_ms=round(elapsed, 1))
                return WakeResult(wake=False, method="vad_silence", elapsed_ms=elapsed)

        # Stage 3: Whisper (fallback)
        method = "whisper_after_vad" if self._vad is not None else "whisper_direct"
        try:
            transcript = await self._stt.transcribe_wake(audio_bytes)
        except Exception as exc:
            _LOGGER.warning("coral_wake.whisper_failed", exc=str(exc))
            return WakeResult(wake=False, method=method,
                              elapsed_ms=(time.perf_counter() - t0) * 1000)

        wake    = self._is_wake_word(transcript)
        elapsed = (time.perf_counter() - t0) * 1000
        _LOGGER.info(
            "coral_wake.result",
            wake=wake, method=method,
            transcript=transcript[:60],
            elapsed_ms=round(elapsed, 1),
        )
        return WakeResult(wake=wake, transcript=transcript,
                          method=method, elapsed_ms=elapsed)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run_coral(self, audio_bytes: bytes):
        """Run the Coral TFLite wake word model synchronously."""
        try:
            pcm = _bytes_to_pcm_f32(audio_bytes)
            inp = self._coral.get_input_details()[0]
            # Model expects fixed-length window; pad or trim to fit
            target = inp["shape"][1] if len(inp["shape"]) > 1 else inp["shape"][0]
            if len(pcm) < target:
                pcm = np.pad(pcm, (0, target - len(pcm)))
            else:
                pcm = pcm[:target]
            tensor = pcm.reshape(inp["shape"]).astype(inp["dtype"])
            self._coral.set_tensor(inp["index"], tensor)
            self._coral.invoke()
            score = float(self._coral.get_tensor(
                self._coral.get_output_details()[0]["index"]
            ).flat[0])
            wake = score >= _CORAL_THRESHOLD
            _LOGGER.info("coral_wake.coral_score", score=round(score, 3), wake=wake)
            return WakeResult(wake=wake, method="coral", score=score)
        except Exception as exc:
            _LOGGER.warning("coral_wake.coral_infer_failed", exc=str(exc))
            return None  # fall through to VAD/Whisper

    def _run_vad(self, audio_bytes: bytes) -> bool:
        """Return True if the audio contains speech above VAD_THRESHOLD."""
        try:
            pcm = _bytes_to_pcm_f32(audio_bytes)
            for i in range(0, len(pcm), _VAD_FRAME_SIZE):
                frame = pcm[i : i + _VAD_FRAME_SIZE]
                if len(frame) < _VAD_FRAME_SIZE:
                    break
                score = float(self._vad.predict(frame))
                if score >= _VAD_THRESHOLD:
                    _LOGGER.debug("coral_wake.vad_speech_detected",
                                  score=round(score, 3))
                    return True
            return False
        except Exception as exc:
            _LOGGER.warning("coral_wake.vad_error", exc=str(exc))
            return True  # fail-safe: pass through to Whisper

    @property
    def coral_available(self) -> bool:
        return self._coral is not None

    @property
    def vad_available(self) -> bool:
        return self._vad is not None

    @property
    def verifier_available(self) -> bool:
        return self._verifier is not None if hasattr(self, "_verifier") else False

    def describe_pipeline(self) -> list[str]:
        """Return a list of active pipeline stages."""
        stages = []
        if self._coral is not None:
            stages.append("coral_tflite")
        if self._vad is not None:
            stages.append("silero_vad")
        if hasattr(self, "_verifier") and self._verifier is not None:
            stages.append("verifier_model")
        stages.append("whisper_fallback")
        return stages

    def reload_verifier(self) -> None:
        """Reload the verifier model from disk (called after training)."""
        verifier_path = _CORAL_MODEL.parent / "nova_verifier.pkl"
        if verifier_path.exists():
            try:
                import pickle
                with open(verifier_path, "rb") as f:
                    self._verifier = pickle.load(f)
                _LOGGER.info("coral_wake.verifier_reloaded", path=str(verifier_path))
            except Exception as exc:
                _LOGGER.warning("coral_wake.verifier_reload_failed", exc=str(exc))
                self._verifier = None
        else:
            self._verifier = None


def _bytes_to_pcm_f32(audio_bytes: bytes) -> np.ndarray:
    """Convert raw bytes (PCM16 or WAV) to float32 PCM in [-1, 1]."""
    # Detect WAV header
    if audio_bytes[:4] == b"RIFF":
        # Skip WAV header (44 bytes standard, but parse properly)
        import wave
        with wave.open(io.BytesIO(audio_bytes)) as wf:
            raw = wf.readframes(wf.getnframes())
    else:
        raw = audio_bytes

    if len(raw) % 2 != 0:
        raw = raw[:-1]
    pcm16 = np.frombuffer(raw, dtype=np.int16)
    return pcm16.astype(np.float32) / 32768.0
