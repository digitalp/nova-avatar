"""Tests for ProactiveService — event filtering, cooldowns, triage parsing."""
from __future__ import annotations
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Patch get_settings before importing ProactiveService
_FAKE_SETTINGS = SimpleNamespace(
    proactive_camera_capture_cooldown_s=60,
)

with patch("avatar_backend.config.get_settings", return_value=_FAKE_SETTINGS):
    from avatar_backend.services.proactive_service import ProactiveService


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_service(**overrides) -> ProactiveService:
    defaults = dict(
        ha_url="http://ha.local:8123",
        ha_token="test-token",
        ha_proxy=MagicMock(),
        llm_service=MagicMock(),
        motion_clip_service=MagicMock(),
        announce_fn=AsyncMock(),
        system_prompt="You are Nova.",
    )
    defaults.update(overrides)
    with patch("avatar_backend.services.proactive_service.load_home_runtime_config") as mock_rt, \
         patch("avatar_backend.services.proactive_service.CoralMotionDetector") as mock_coral, \
         patch("avatar_backend.config.get_settings", return_value=_FAKE_SETTINGS):
        mock_rt.return_value = SimpleNamespace(
            motion_camera_map={},
            bypass_global_motion_cameras=set(),
            camera_vision_prompts={},
            exclude_entities=set(),
            weather_entity="weather.home",
            phone_notify_services=[],
        )
        mock_coral.build.return_value = MagicMock(enabled=False)
        return ProactiveService(**defaults)


def _state_changed_msg(entity_id, old_state, new_state, attrs=None):
    return {
        "type": "event",
        "event": {
            "event_type": "state_changed",
            "data": {
                "entity_id": entity_id,
                "old_state": {"state": old_state, "attributes": attrs or {}},
                "new_state": {"state": new_state, "attributes": attrs or {}},
            },
        },
    }


# ── Event filtering ─────────────────────────────────────────────────────────

def test_ignores_non_event_messages():
    svc = _make_service()
    svc._on_message({"type": "result"})
    assert len(svc._queue) == 0


def test_ignores_same_state():
    svc = _make_service()
    svc._on_message(_state_changed_msg("binary_sensor.door", "off", "off"))
    assert len(svc._queue) == 0


def test_ignores_unavailable_states():
    svc = _make_service()
    svc._on_message(_state_changed_msg("lock.front", "locked", "unavailable"))
    assert len(svc._queue) == 0


def test_ignores_unknown_states():
    svc = _make_service()
    svc._on_message(_state_changed_msg("lock.front", "unknown", "locked"))
    assert len(svc._queue) == 0


def test_ignores_unwatched_domains():
    svc = _make_service()
    svc._on_message(_state_changed_msg("light.kitchen", "off", "on"))
    assert len(svc._queue) == 0


def test_queues_lock_state_change():
    svc = _make_service()
    svc._on_message(_state_changed_msg("lock.front_door", "locked", "unlocked"))
    assert len(svc._queue) == 1
    assert svc._queue[0]["entity_id"] == "lock.front_door"


def test_queues_alarm_state_change():
    svc = _make_service()
    svc._on_message(_state_changed_msg(
        "alarm_control_panel.home", "armed_away", "triggered"
    ))
    assert len(svc._queue) == 1


def test_binary_sensor_only_queues_on():
    svc = _make_service()
    # off→on should queue
    svc._on_message(_state_changed_msg("binary_sensor.smoke", "off", "on",
                                        {"device_class": "smoke"}))
    assert len(svc._queue) == 1
    # on→off should NOT queue
    svc._on_message(_state_changed_msg("binary_sensor.smoke", "on", "off",
                                        {"device_class": "smoke"}))
    assert len(svc._queue) == 1  # still 1


def test_motion_sensor_never_queued():
    """Motion sensors should be handled by camera path, not batch triage."""
    svc = _make_service()
    svc._on_message(_state_changed_msg(
        "binary_sensor.motion_front", "off", "on",
        {"device_class": "motion"}
    ))
    assert len(svc._queue) == 0


def test_excluded_entity_ignored():
    svc = _make_service()
    svc._exclude_entities.add("binary_sensor.noisy_sensor")
    svc._on_message(_state_changed_msg(
        "binary_sensor.noisy_sensor", "off", "on",
        {"device_class": "smoke"}
    ))
    assert len(svc._queue) == 0


def test_climate_queues_meaningful_transitions():
    svc = _make_service()
    svc._on_message(_state_changed_msg("climate.living_room", "off", "heat"))
    assert len(svc._queue) == 1


def test_climate_ignores_same_mode():
    svc = _make_service()
    svc._on_message(_state_changed_msg("climate.living_room", "heat", "heat"))
    assert len(svc._queue) == 0


# ── Cooldown logic ───────────────────────────────────────────────────────────

def test_entity_cooldown_prevents_requeue():
    svc = _make_service()
    svc._cooldowns["lock.front_door"] = time.monotonic()
    svc._on_message(_state_changed_msg("lock.front_door", "locked", "unlocked"))
    assert len(svc._queue) == 0


def test_queue_dedup_cooldown():
    svc = _make_service()
    svc._on_message(_state_changed_msg("lock.front_door", "locked", "unlocked"))
    assert len(svc._queue) == 1
    # Second event within dedup window should be dropped
    svc._on_message(_state_changed_msg("lock.front_door", "unlocked", "locked"))
    assert len(svc._queue) == 1  # not 2


def test_camera_capture_cooldown():
    svc = _make_service()
    svc._motion_camera_map["binary_sensor.motion_drive"] = "camera.driveway"
    # First motion event — should trigger (creates task)
    svc._camera_cooldowns["camera.driveway"] = 0
    with patch("asyncio.create_task"):
        svc._on_message(_state_changed_msg(
            "binary_sensor.motion_drive", "off", "on",
            {"device_class": "motion"}
        ))
    # Camera cooldown should now be set
    assert "camera.driveway" in svc._camera_cooldowns
    assert svc._camera_cooldowns["camera.driveway"] > 0

    # Second event within cooldown — should be dropped
    with patch("asyncio.create_task") as mock_task:
        svc._on_message(_state_changed_msg(
            "binary_sensor.motion_drive", "off", "on",
            {"device_class": "motion"}
        ))
        mock_task.assert_not_called()
    # Queue should still be empty (motion never goes to queue)
    assert len(svc._queue) == 0


# ── Triage ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_triage_announces_on_llm_yes():
    svc = _make_service()
    svc._llm.generate_text_local_fast_resilient = AsyncMock(
        return_value='{"announce": true, "message": "Front door unlocked!", "priority": "alert"}'
    )
    svc._ha.get_entity_state = AsyncMock(return_value={"state": "unlocked"})

    changes = [{"entity_id": "lock.front_door", "friendly": "Front Door",
                "old": "locked", "new": "unlocked", "queued_at": time.monotonic()}]
    await svc._triage(changes)
    svc._announce.assert_awaited_once()
    call_args = svc._announce.call_args[0]
    assert "Front door unlocked" in call_args[0]


@pytest.mark.asyncio
async def test_triage_silent_on_llm_no():
    svc = _make_service()
    svc._llm.generate_text_local_fast_resilient = AsyncMock(
        return_value='{"announce": false}'
    )
    svc._ha.get_entity_state = AsyncMock(return_value={"state": "on"})

    changes = [{"entity_id": "binary_sensor.door", "friendly": "Door",
                "old": "off", "new": "on", "queued_at": time.monotonic()}]
    await svc._triage(changes)
    svc._announce.assert_not_awaited()


@pytest.mark.asyncio
async def test_triage_handles_markdown_fenced_json():
    svc = _make_service()
    svc._llm.generate_text_local_fast_resilient = AsyncMock(
        return_value='```json\n{"announce": true, "message": "Alert!", "priority": "normal"}\n```'
    )
    svc._ha.get_entity_state = AsyncMock(return_value={"state": "unlocked"})

    changes = [{"entity_id": "lock.front", "friendly": "Front Lock",
                "old": "locked", "new": "unlocked", "queued_at": time.monotonic()}]
    await svc._triage(changes)
    svc._announce.assert_awaited_once()


@pytest.mark.asyncio
async def test_triage_drops_resolved_binary_sensor():
    """If a binary_sensor returned to off before triage, skip it."""
    svc = _make_service()
    svc._ha.get_entity_state = AsyncMock(return_value={"state": "off"})
    svc._llm.generate_text_local_fast_resilient = AsyncMock()

    changes = [{"entity_id": "binary_sensor.door", "friendly": "Door",
                "old": "off", "new": "on", "queued_at": time.monotonic()}]
    await svc._triage(changes)
    # LLM should never be called since all events resolved
    svc._llm.generate_text_local_fast_resilient.assert_not_awaited()


@pytest.mark.asyncio
async def test_triage_respects_global_announce_cooldown():
    svc = _make_service()
    svc._last_announce_time = time.monotonic()  # just announced
    svc._llm.generate_text_local_fast_resilient = AsyncMock()

    changes = [{"entity_id": "lock.front", "friendly": "Front Lock",
                "old": "locked", "new": "unlocked", "queued_at": time.monotonic()}]
    await svc._triage(changes)
    svc._llm.generate_text_local_fast_resilient.assert_not_awaited()


@pytest.mark.asyncio
async def test_triage_handles_bad_json():
    svc = _make_service()
    svc._llm.generate_text_local_fast_resilient = AsyncMock(
        return_value="I think everything is fine, no need to announce."
    )
    svc._ha.get_entity_state = AsyncMock(return_value={"state": "unlocked"})

    changes = [{"entity_id": "lock.front", "friendly": "Front Lock",
                "old": "locked", "new": "unlocked", "queued_at": time.monotonic()}]
    await svc._triage(changes)
    svc._announce.assert_not_awaited()


@pytest.mark.asyncio
async def test_triage_handles_llm_failure():
    svc = _make_service()
    svc._llm.generate_text_local_fast_resilient = AsyncMock(
        side_effect=RuntimeError("Ollama down")
    )
    svc._ha.get_entity_state = AsyncMock(return_value={"state": "unlocked"})

    changes = [{"entity_id": "lock.front", "friendly": "Front Lock",
                "old": "locked", "new": "unlocked", "queued_at": time.monotonic()}]
    # Should not raise
    await svc._triage(changes)
    svc._announce.assert_not_awaited()


# ── Weather filtering ────────────────────────────────────────────────────────

def test_weather_entity_not_queued():
    svc = _make_service()
    svc._weather_entity = "weather.home"
    # Weather changes should be handled by weather monitor, not batch queue
    with patch("asyncio.create_task"):
        svc._on_message(_state_changed_msg("weather.home", "sunny", "rainy"))
    assert len(svc._queue) == 0
