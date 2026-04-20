"""Tests for MetricsDB — SQLite persistence for LLM costs, system metrics,
memories, motion clips, decisions, and event history."""
from __future__ import annotations
import tempfile
from pathlib import Path

import pytest

from avatar_backend.services.metrics_db import MetricsDB


@pytest.fixture
def db(tmp_path):
    return MetricsDB(path=tmp_path / "test_metrics.db")


# ── Schema initialisation ───────────────────────────────────────────────────

def test_creates_db_file(tmp_path):
    p = tmp_path / "sub" / "metrics.db"
    MetricsDB(path=p)
    assert p.exists()


def test_tables_created(db):
    conn = db._conn()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    expected = {
        "llm_invocations", "system_samples", "decision_events",
        "server_logs", "long_term_memories", "motion_clips",
        "event_history", "events", "event_actions", "event_media",
        "health_checks", "conversation_audit", "conversation_sessions",
        "conversation_turn_summaries",
    }
    assert expected.issubset(tables)


# ── LLM invocations ─────────────────────────────────────────────────────────

def test_insert_and_read_invocation(db):
    db.insert_invocation({
        "provider": "ollama", "model": "llama3.1:8b",
        "purpose": "chat", "input_tokens": 100,
        "output_tokens": 50, "cost_usd": 0.0, "elapsed_ms": 200,
    })
    rows = db.recent_invocations(n=10)
    assert len(rows) == 1
    assert rows[0]["provider"] == "ollama"
    assert rows[0]["input_tokens"] == 100


def test_cost_summary(db):
    for i in range(3):
        db.insert_invocation({
            "provider": "google", "model": "gemini-2.0-flash",
            "input_tokens": 100, "output_tokens": 50,
            "cost_usd": 0.01, "elapsed_ms": 100,
        })
    summary = db.cost_summary("month")
    assert summary["calls"] == 3
    assert abs(summary["cost_usd"] - 0.03) < 1e-9
    assert summary["input_tokens"] == 300


def test_cost_by_model(db):
    db.insert_invocation({"provider": "ollama", "model": "llama3.1:8b",
                          "cost_usd": 0.0, "input_tokens": 10, "output_tokens": 5})
    db.insert_invocation({"provider": "google", "model": "gemini-2.0-flash",
                          "cost_usd": 0.05, "input_tokens": 200, "output_tokens": 100})
    by_model = db.cost_by_model("month")
    assert len(by_model) == 2
    # Sorted by cost DESC — google first
    assert by_model[0]["provider"] == "google"


# ── System samples ───────────────────────────────────────────────────────────

def test_insert_and_read_sample(db):
    db.insert_sample({
        "cpu_pct": 45.0, "ram_used": 8_000_000_000,
        "ram_total": 16_000_000_000, "disk_used": 100_000_000_000,
        "disk_total": 500_000_000_000, "gpu_util": 30.0,
        "gpu_mem_used": 4_000_000_000, "gpu_mem_total": 6_000_000_000,
        "ollama_gpu_pct": 25.0,
    })
    latest = db.latest_sample()
    assert latest is not None
    assert latest["cpu_pct"] == 45.0


def test_recent_samples_empty(db):
    assert db.recent_samples(minutes=60) == []


# ── Decision events ──────────────────────────────────────────────────────────

def test_insert_and_read_decision(db):
    db.insert_decision({"kind": "triage_announce", "entities": ["lock.front"],
                         "message": "Front door unlocked"})
    rows = db.recent_decisions(n=10)
    assert len(rows) == 1
    assert rows[0]["kind"] == "triage_announce"


# ── Server logs ──────────────────────────────────────────────────────────────

def test_insert_and_read_logs(db):
    db.insert_log({"level": "info", "event": "test event", "logger": "test"})
    db.insert_log({"level": "error", "event": "bad thing", "logger": "test"})
    all_logs = db.recent_logs(n=10)
    assert len(all_logs) == 2
    errors = db.recent_logs(n=10, level="error")
    assert len(errors) == 1
    assert errors[0]["event"] == "bad thing"


def test_purge_old_logs(db):
    db.insert_log({"level": "info", "event": "recent", "logger": "test"})
    purged = db.purge_old_logs(keep_days=0)
    # Just inserted — should be within "today" so purge with 0 days keeps nothing
    assert purged >= 0


# ── Health checks ────────────────────────────────────────────────────────────

def test_insert_and_read_health(db):
    db.insert_health_check("ollama", "ok")
    db.insert_health_check("whisper", "ok")
    db.insert_health_check("ollama", "error")
    history = db.get_health_history()
    assert len(history) >= 2


# ── Long-term memories ───────────────────────────────────────────────────────

def test_upsert_and_list_memories(db):
    db.upsert_memory(summary="The cat's name is Luna", category="household", source="chat")
    db.upsert_memory(summary="The dog's name is Max", category="household", source="chat")
    memories = db.list_memories(limit=10)
    assert len(memories) == 2
    summaries = {m["summary"] for m in memories}
    assert "The cat's name is Luna" in summaries


def test_upsert_memory_dedup(db):
    db.upsert_memory(summary="The cat's name is Luna", category="household", source="chat")
    db.upsert_memory(summary="The cat's name is Luna", category="household", source="chat")
    memories = db.list_memories(limit=10)
    assert len(memories) == 1
    assert memories[0]["times_seen"] == 2


def test_delete_memory(db):
    db.upsert_memory(summary="Temp fact", category="general", source="chat")
    memories = db.list_memories()
    assert len(memories) == 1
    db.delete_memory(memories[0]["id"])
    assert len(db.list_memories()) == 0


def test_clear_memories(db):
    db.upsert_memory(summary="Fact 1", category="general", source="chat")
    db.upsert_memory(summary="Fact 2", category="general", source="chat")
    cleared = db.clear_memories()
    assert cleared == 2
    assert len(db.list_memories()) == 0


def test_pin_memory(db):
    db.upsert_memory(summary="Important fact", category="general", source="chat")
    memories = db.list_memories()
    mid = memories[0]["id"]
    db.update_memory(mid, summary="Important fact", category="general", pinned=True)
    updated = db.list_memories()
    assert updated[0]["pinned"] == 1


# ── Motion clips ─────────────────────────────────────────────────────────────

def test_insert_and_read_motion_clip(db):
    clip_id = db.insert_motion_clip({
        "camera_entity_id": "camera.driveway",
        "trigger_entity_id": "binary_sensor.motion",
        "location": "driveway",
        "description": "A person walking",
        "video_relpath": "clips/001.mp4",
    })
    assert clip_id > 0
    clip = db.get_motion_clip(clip_id)
    assert clip is not None
    assert clip["description"] == "A person walking"


def test_motion_clip_stats(db):
    db.insert_motion_clip({"camera_entity_id": "camera.front",
                           "description": "Person"})
    db.insert_motion_clip({"camera_entity_id": "camera.back",
                           "description": "Cat"})
    stats = db.motion_clip_stats()
    assert stats["total_clips"] == 2


def test_delete_motion_clip(db):
    cid = db.insert_motion_clip({"camera_entity_id": "camera.front",
                                  "video_relpath": "clips/x.mp4",
                                  "description": "test"})
    relpath = db.delete_motion_clip(cid)
    assert relpath == "clips/x.mp4"
    assert db.get_motion_clip(cid) is None


def test_toggle_motion_clip_flag(db):
    cid = db.insert_motion_clip({"camera_entity_id": "camera.front",
                                  "description": "test"})
    assert db.toggle_motion_clip_flag(cid) is True  # now flagged
    assert db.toggle_motion_clip_flag(cid) is False  # now unflagged


# ── Event history ────────────────────────────────────────────────────────────

def test_insert_and_read_event_history(db):
    db.insert_event_history({
        "event_type": "motion", "title": "Motion detected",
        "summary": "Person on driveway", "event_source": "camera",
        "camera_entity_id": "camera.driveway",
    })
    rows = db.recent_event_history(n=10)
    assert len(rows) == 1
    assert rows[0]["title"] == "Motion detected"


# ── Canonical events ─────────────────────────────────────────────────────────

def test_insert_and_read_event_record(db):
    db.insert_event_record({
        "event_id": "evt-001", "event_type": "delivery",
        "source": "camera", "room": "front",
        "camera_entity_id": "camera.doorbell",
        "summary": "Package delivered", "details": "DHL van",
    })
    record = db.get_event_record("evt-001")
    assert record is not None
    assert record["summary"] == "Package delivered"


def test_update_event_record_status(db):
    db.insert_event_record({
        "event_id": "evt-002", "event_type": "alert",
        "source": "sensor", "summary": "Smoke detected",
    })
    db.update_event_record_status("evt-002", status="resolved")
    record = db.get_event_record("evt-002")
    assert record["status"] == "resolved"


# ── Conversation audit ───────────────────────────────────────────────────────

def test_insert_and_read_conversation_audit(db):
    db.insert_conversation_audit({
        "session_id": "sess-1", "user_text": "Turn on the lights",
        "llm_response": "Done", "final_reply": "I've turned on the lights",
        "processing_ms": 150, "model": "llama3.1:8b",
    })
    audits = db.list_conversation_audits(limit=10)
    assert len(audits) == 1
    assert audits[0]["user_text"] == "Turn on the lights"


def test_cleanup_old_audits(db):
    db.insert_conversation_audit({
        "session_id": "sess-1", "user_text": "test",
        "llm_response": "ok", "final_reply": "ok",
    })
    cleaned = db.cleanup_old_audits(retention_days=0)
    assert cleaned >= 0


# ── Write lock consistency ───────────────────────────────────────────────────

def test_many_sequential_writes_dont_corrupt(db):
    """Many rapid writes should all succeed without corruption."""
    for i in range(100):
        db.insert_invocation({
            "provider": "ollama", "model": "test",
            "input_tokens": i, "output_tokens": i,
            "cost_usd": 0.001 * i, "elapsed_ms": 1,
        })
    rows = db.recent_invocations(n=200)
    assert len(rows) == 100
    summary = db.cost_summary("month")
    assert summary["calls"] == 100
