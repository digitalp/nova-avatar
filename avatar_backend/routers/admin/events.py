"""Events sub-router: event-history, workflow-summary, workflow-status, action, workflow-run, domain-action."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, Request

from avatar_backend.bootstrap.container import AppContainer, get_container

from avatar_backend.services.action_service import ActionService
from avatar_backend.services.open_loop_workflow_service import OpenLoopWorkflowService

from .common import (
    _OPEN_LOOP_SERVICE,
    _get_session,
    _require_session,
    EventHistoryActionBody,
    EventHistoryWorkflowRunBody,
    EventHistoryDomainActionBody,
)
from .motion import _serialize_motion_clip, _filter_playable

_LOGGER = structlog.get_logger()
router = APIRouter()


# ── Serialisation helpers ─────────────────────────────────────────────────────

def _surface_event_iso_ts(value) -> str:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _serialize_event_history_item(item: dict) -> dict:
    open_loop = _OPEN_LOOP_SERVICE.extract_summary_fields(
        item.get("data") or {},
        status=str(item.get("status") or ""),
        fallback_ts=str(item.get("ts") or ""),
    )
    action_service = ActionService(open_loop_service=_OPEN_LOOP_SERVICE)
    payload = {
        "id": item.get("id", ""),
        "kind": item.get("kind", "event"),
        "ts": item.get("ts", ""),
        "title": item.get("title", ""),
        "summary": item.get("summary", ""),
        "status": item.get("status", ""),
        "event_id": item.get("event_id", ""),
        "event_type": item.get("event_type", ""),
        "event_source": item.get("event_source", ""),
        "camera_entity_id": item.get("camera_entity_id", ""),
        "clip_id": item.get("clip_id"),
        "video_url": item.get("video_url", ""),
        "open_loop_note": open_loop["open_loop_note"] or item.get("open_loop_note", ""),
        "open_loop_state": open_loop["open_loop_state"],
        "open_loop_active": open_loop["open_loop_active"],
        "open_loop_started_ts": open_loop["open_loop_started_ts"],
        "open_loop_updated_ts": open_loop["open_loop_updated_ts"],
        "open_loop_resolved_ts": open_loop["open_loop_resolved_ts"],
        "open_loop_age_s": open_loop["open_loop_age_s"],
        "open_loop_stale": open_loop["open_loop_stale"],
        "open_loop_last_reminder_ts": open_loop["open_loop_last_reminder_ts"],
        "open_loop_reminder_count": open_loop["open_loop_reminder_count"],
        "open_loop_reminder_due": open_loop["open_loop_reminder_due"],
        "open_loop_reminder_state": open_loop["open_loop_reminder_state"],
        "open_loop_last_escalation_ts": open_loop["open_loop_last_escalation_ts"],
        "open_loop_escalation_level": open_loop["open_loop_escalation_level"],
        "open_loop_escalation_due": open_loop["open_loop_escalation_due"],
        "open_loop_priority": open_loop["open_loop_priority"],
        "data": item.get("data") or {},
    }
    payload["available_actions"] = action_service.build_event_history_actions(payload)
    return payload


def _default_open_loop_note(status: str, workflow_action: str | None = None) -> str:
    if workflow_action:
        return _OPEN_LOOP_SERVICE.default_note_for_workflow_action(workflow_action)
    return {
        "active": "Needs attention",
        "acknowledged": "Seen by admin",
        "resolved": "Closed out",
    }.get(status, "")


# ── Event history ─────────────────────────────────────────────────────────────

@router.get("/event-history")
async def get_event_history(
    request: Request,
    limit: int = 20,
    query: str | None = None,
    kind: str | None = None,
    event_type: str | None = None,
    event_source: str | None = None,
    status: str | None = None,
    open_loop_state: str | None = None,
    open_loop_only: bool = False,
    open_loop_stale_only: bool = False,
    open_loop_priority: str | None = None,
    open_loop_reminder_due_only: bool = False,
    open_loop_escalation_due_only: bool = False,
    window: str | None = None,
    before_ts: str | None = None,
    container: AppContainer = Depends(get_container),
):
    _require_session(request, min_role="viewer")
    db = container.metrics_db
    surface_state = getattr(container, "surface_state_service", None)

    rows: list[dict] = []

    if db is not None:
        canonical_events = []
        if hasattr(db, "list_event_records"):
            canonical_events = db.list_event_records(limit=max(1, min(limit * 3, 120)))
        for event in canonical_events:
            rows.append(
                _serialize_event_history_item(
                    {
                        "id": f"canonical:{event.get('event_id') or event.get('created_at')}",
                        "kind": "canonical_event",
                        "ts": event.get("created_at", ""),
                        "title": event.get("details") or event.get("summary") or event.get("event_type", ""),
                        "summary": event.get("summary", ""),
                        "status": event.get("status", ""),
                        "event_id": event.get("event_id", ""),
                        "event_type": event.get("event_type", ""),
                        "event_source": event.get("source", ""),
                        "camera_entity_id": event.get("camera_entity_id", ""),
                        "clip_id": None,
                        "video_url": "",
                        "open_loop_note": str((event.get("data") or {}).get("open_loop_note", "")),
                        "data": event.get("data") or {},
                    }
                )
            )

    if db is not None:
        persisted_events = db.recent_event_history(max(1, min(limit * 3, 120)))
        for event in persisted_events:
            rows.append(
                _serialize_event_history_item(
                    {
                        "id": f"persisted:{event.get('event_id') or event.get('ts')}",
                        "kind": "persisted_event",
                        "ts": event.get("ts", ""),
                        "title": event.get("title", ""),
                        "summary": event.get("summary", ""),
                        "status": event.get("status", ""),
                        "event_id": event.get("event_id", ""),
                        "event_type": event.get("event_type", ""),
                        "event_source": event.get("event_source", ""),
                        "camera_entity_id": event.get("camera_entity_id", ""),
                        "clip_id": None,
                        "video_url": "",
                        "open_loop_note": str((event.get("data") or {}).get("open_loop_note", "")),
                        "data": event.get("data") or {},
                    }
                )
            )

    if db is not None:
        motion_clips = db.recent_motion_clips(limit=max(1, min(limit * 3, 120)))
        motion_clips = await _filter_playable(request, motion_clips)
        for clip in motion_clips:
            payload = _serialize_motion_clip(clip)
            rows.append(
                _serialize_event_history_item(
                    {
                        "id": f"motion:{payload.get('id')}",
                        "kind": "motion_clip",
                        "ts": payload.get("ts", ""),
                        "title": payload.get("location") or payload.get("canonical_event_type") or "Motion event",
                        "summary": payload.get("description", ""),
                        "status": payload.get("status", ""),
                        "event_id": payload.get("canonical_event_id", ""),
                        "event_type": payload.get("canonical_event_type", ""),
                        "event_source": payload.get("event_source", ""),
                        "camera_entity_id": payload.get("camera_entity_id", ""),
                        "clip_id": payload.get("id"),
                        "video_url": payload.get("video_url", ""),
                        "open_loop_note": str(payload.get("extra", {}).get("open_loop_note", "")),
                        "data": {
                            "location": payload.get("location", ""),
                            "trigger_entity_id": payload.get("trigger_entity_id", ""),
                            "duration_s": payload.get("duration_s", 0),
                            "canonical_event": payload.get("canonical_event") or {},
                            "extra": payload.get("extra") or {},
                        },
                    }
                )
            )

    if surface_state is not None:
        snapshot = await surface_state.get_snapshot()
        for event in snapshot.get("recent_events", []):
            rows.append(
                _serialize_event_history_item(
                    {
                        "id": f"surface:{event.get('event_id', '')}",
                        "kind": "surface_event",
                        "ts": _surface_event_iso_ts(event.get("ts")),
                        "title": event.get("title") or event.get("event") or "Event",
                        "summary": event.get("message", ""),
                        "status": event.get("status", ""),
                        "event_id": event.get("event_id", ""),
                        "event_type": event.get("event", ""),
                        "event_source": "surface_state",
                        "camera_entity_id": event.get("camera_entity_id", ""),
                        "clip_id": None,
                        "video_url": "",
                        "open_loop_note": event.get("open_loop_note", ""),
                        "data": dict(event),
                    }
                )
            )

    rows.sort(key=lambda item: item.get("ts", ""), reverse=True)
    query_norm = (query or "").strip().lower()
    deduped: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        dedupe_key = str(row.get("event_id") or row.get("id") or "")
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        if kind and str(row.get("kind") or "") != kind:
            continue
        if event_type and str(row.get("event_type") or "") != event_type:
            continue
        if event_source and str(row.get("event_source") or "") != event_source:
            continue
        if status and str(row.get("status") or "") != status:
            continue
        if open_loop_state and str(row.get("open_loop_state") or "") != open_loop_state:
            continue
        if open_loop_only and not bool(row.get("open_loop_active")):
            continue
        if open_loop_stale_only and not bool(row.get("open_loop_stale")):
            continue
        if open_loop_priority and str(row.get("open_loop_priority") or "") != open_loop_priority:
            continue
        if open_loop_reminder_due_only and not bool(row.get("open_loop_reminder_due")):
            continue
        if open_loop_escalation_due_only and not bool(row.get("open_loop_escalation_due")):
            continue
        if query_norm:
            haystack = " ".join(
                [
                    str(row.get("title") or ""),
                    str(row.get("summary") or ""),
                    str(row.get("event_type") or ""),
                    str(row.get("event_source") or ""),
                    str(row.get("open_loop_note") or ""),
                    str((row.get("data") or {}).get("admin_note") or ""),
                ]
            ).lower()
            if query_norm not in haystack:
                continue
        deduped.append(row)
        if len(deduped) >= max(1, min(limit, 100)):
            break

    if before_ts:
        deduped = [row for row in deduped if str(row.get("ts") or "") < before_ts]

    if window:
        now = datetime.now(timezone.utc)
        hours = {
            "24h": 24,
            "3d": 72,
            "7d": 168,
            "30d": 720,
        }.get(window)
        if hours:
            cutoff = (now - timedelta(hours=hours)).isoformat()
            deduped = [row for row in deduped if str(row.get("ts") or "") >= cutoff]

    deduped = deduped[: max(1, min(limit, 100))]
    next_before = deduped[-1]["ts"] if deduped else None
    return {"events": deduped, "next_before_ts": next_before}


@router.get("/event-history/workflow-summary")
async def get_event_history_workflow_summary(request: Request, limit: int = 10, container: AppContainer = Depends(get_container)):
    _require_session(request, min_role="viewer")
    workflow_service = getattr(container, "open_loop_workflow_service", None)
    if workflow_service is None:
        workflow_service = OpenLoopWorkflowService(open_loop_service=_OPEN_LOOP_SERVICE)

    history = await get_event_history(
        request,
        limit=max(20, min(limit * 6, 120)),
        open_loop_only=True,
        window="30d",
        container=container,
    )
    persisted_rows = [row for row in history.get("events", []) if row.get("kind") == "persisted_event"]
    summary = workflow_service.summarize_due_work(persisted_rows, limit=max(1, min(limit, 20)))
    summary["generated_from"] = {"kind": "persisted_event", "count": len(persisted_rows)}
    return summary


@router.get("/event-history/workflow-status")
async def get_event_history_workflow_status(request: Request, container: AppContainer = Depends(get_container)):
    _require_session(request, min_role="viewer")
    automation_service = getattr(container, "open_loop_automation_service", None)
    if automation_service is None:
        return {"running": False, "last_run_ts": "", "last_run_summary": {"planned": 0, "applied": 0, "applied_actions": []}}
    return automation_service.get_status()


@router.post("/event-history/action")
async def update_event_history_action(body: EventHistoryActionBody, request: Request, container: AppContainer = Depends(get_container)):
    _require_session(request, min_role="viewer")
    ws_mgr = getattr(container, "ws_manager", None)
    action_service = getattr(container, "action_service", None) or ActionService()

    event_id = (body.event_id or "").strip()
    open_loop_note = body.open_loop_note if body.open_loop_note is not None else _default_open_loop_note(body.status, body.workflow_action)
    return await action_service.handle_event_history_action(
        app=request.app,
        ws_mgr=ws_mgr,
        event_id=event_id,
        status=body.status,
        workflow_action=body.workflow_action,
        title=body.title,
        summary=body.summary,
        event_type=body.event_type,
        event_source=body.event_source,
        camera_entity_id=body.camera_entity_id,
        open_loop_note=open_loop_note,
        admin_note=body.admin_note,
        reminder_sent=body.reminder_sent,
        escalation_level=body.escalation_level,
    )


@router.post("/event-history/workflow-run")
async def run_event_history_workflow(body: EventHistoryWorkflowRunBody, request: Request, container: AppContainer = Depends(get_container)):
    _require_session(request, min_role="viewer")
    workflow_service = getattr(container, "open_loop_workflow_service", None)
    if workflow_service is None:
        workflow_service = OpenLoopWorkflowService(open_loop_service=_OPEN_LOOP_SERVICE)
    action_service = getattr(container, "action_service", None) or ActionService()
    ws_mgr = getattr(container, "ws_manager", None)

    history = await get_event_history(
        request,
        limit=max(20, min(body.limit * 8, 160)),
        open_loop_only=True,
        window="30d",
        container=container,
    )
    persisted_rows = [row for row in history.get("events", []) if row.get("kind") == "persisted_event"]
    planned = workflow_service.plan_due_actions(
        persisted_rows,
        include_reminders=body.include_reminders,
        include_escalations=body.include_escalations,
        limit=max(1, min(body.limit, 25)),
    )
    if body.dry_run:
        return {"planned": planned, "applied": [], "dry_run": True}

    applied: list[dict] = []
    for item in planned:
        applied.append(
            await action_service.handle_event_history_action(
                app=request.app,
                ws_mgr=ws_mgr,
                event_id=str(item.get("event_id") or ""),
                status=str(item.get("status") or "active"),
                workflow_action=str(item.get("workflow_action") or ""),
                title=str(item.get("title") or ""),
                summary=str(item.get("summary") or ""),
                event_type=str(item.get("event_type") or ""),
                event_source=str(item.get("event_source") or ""),
                open_loop_note=str(item.get("open_loop_note") or ""),
            )
        )
    return {"planned": planned, "applied": applied, "dry_run": False}


@router.post("/event-history/domain-action")
async def run_event_history_domain_action(body: EventHistoryDomainActionBody, request: Request, container: AppContainer = Depends(get_container)):
    _require_session(request, min_role="viewer")
    ws_mgr = getattr(container, "ws_manager", None)
    action_service = getattr(container, "action_service", None) or ActionService()
    return await action_service.handle_event_history_domain_action(
        app=request.app,
        ws_mgr=ws_mgr,
        session_id=(body.session_id or "admin_event_history").strip() or "admin_event_history",
        event_id=(body.event_id or "").strip(),
        action=body.action,
        title=body.title,
        summary=body.summary,
        event_type=body.event_type,
        event_source=body.event_source,
        camera_entity_id=body.camera_entity_id,
        followup_prompt=body.followup_prompt,
        target_camera_entity_id=body.target_camera_entity_id,
        target_event=body.target_event,
        target_title=body.target_title,
        target_message=body.target_message,
    )
