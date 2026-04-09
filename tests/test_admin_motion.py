from types import SimpleNamespace

import pytest

from avatar_backend.routers.admin import _serialize_motion_clip, get_event_history


def test_serialize_motion_clip_exposes_canonical_event_fields():
    clip = _serialize_motion_clip(
        {
            "id": 7,
            "video_relpath": "2026/04/08/example.mp4",
            "extra": {
                "source": "announce_motion",
                "canonical_event": {
                    "event_id": "evt-7",
                    "event_type": "motion_detected",
                    "event_context": {"source": "announce_motion"},
                },
            },
        }
    )

    assert clip["video_url"] == "/admin/motion-clips/7/video"
    assert clip["canonical_event_id"] == "evt-7"
    assert clip["canonical_event_type"] == "motion_detected"
    assert clip["event_source"] == "announce_motion"


class _FakeSurfaceState:
    async def get_snapshot(self):
        return {
            "recent_events": [
                {
                    "event_id": "evt-surface-1",
                    "event": "doorbell",
                    "title": "Doorbell",
                    "message": "Front door live view",
                    "status": "active",
                    "camera_entity_id": "camera.front_door",
                    "ts": 1712600000.0,
                }
            ]
        }


class _FakeDB:
    def recent_event_history(self, n=20):
        return [
            {
                "ts": "2026-04-08T20:05:00+00:00",
                "event_id": "evt-persisted-1",
                "event_type": "doorbell",
                "title": "Doorbell",
                "summary": "Front door live view",
                "status": "active",
                "event_source": "doorbell",
                "camera_entity_id": "camera.front_door",
                "data": {},
            }
        ]

    def recent_motion_clips(self, limit=60):
        return [
            {
                "id": 12,
                "ts": "2026-04-08T20:00:00+00:00",
                "camera_entity_id": "camera.driveway",
                "location": "Driveway",
                "description": "A car arrived.",
                "status": "ready",
                "video_relpath": "2026/04/08/example.mp4",
                "extra": {
                    "source": "announce_motion",
                    "canonical_event": {
                        "event_id": "evt-motion-12",
                        "event_type": "vehicle_detected",
                        "event_context": {"source": "announce_motion"},
                    },
                },
            }
        ]


@pytest.mark.asyncio
async def test_event_history_combines_motion_and_surface_events(monkeypatch):
    from avatar_backend.routers import admin as admin_module

    monkeypatch.setattr(admin_module, "_require_session", lambda request, min_role="viewer": {"role": "viewer"})
    monkeypatch.setattr(admin_module, "_motion_clip_is_playable", lambda request, clip: True)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                metrics_db=_FakeDB(),
                surface_state_service=_FakeSurfaceState(),
            )
        )
    )

    data = await get_event_history(request, limit=10)
    assert len(data["events"]) == 3
    assert data["events"][0]["kind"] == "persisted_event"
    assert data["events"][0]["event_type"] == "doorbell"
    assert data["events"][1]["kind"] == "motion_clip"
    assert data["events"][1]["event_type"] == "vehicle_detected"
    assert data["events"][2]["kind"] == "surface_event"


@pytest.mark.asyncio
async def test_event_history_filters_by_kind_and_source(monkeypatch):
    from avatar_backend.routers import admin as admin_module

    monkeypatch.setattr(admin_module, "_require_session", lambda request, min_role="viewer": {"role": "viewer"})
    monkeypatch.setattr(admin_module, "_motion_clip_is_playable", lambda request, clip: True)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                metrics_db=_FakeDB(),
                surface_state_service=_FakeSurfaceState(),
            )
        )
    )

    data = await get_event_history(request, limit=10, kind="persisted_event", event_source="doorbell")
    assert len(data["events"]) == 1
    assert data["events"][0]["kind"] == "persisted_event"
    assert data["events"][0]["event_source"] == "doorbell"


@pytest.mark.asyncio
async def test_event_history_supports_before_ts_window(monkeypatch):
    from avatar_backend.routers import admin as admin_module

    monkeypatch.setattr(admin_module, "_require_session", lambda request, min_role="viewer": {"role": "viewer"})
    monkeypatch.setattr(admin_module, "_motion_clip_is_playable", lambda request, clip: True)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                metrics_db=_FakeDB(),
                surface_state_service=_FakeSurfaceState(),
            )
        )
    )

    data = await get_event_history(request, limit=10, before_ts="2026-04-08T20:03:00+00:00", window="30d")
    assert all(event["ts"] < "2026-04-08T20:03:00+00:00" for event in data["events"])
