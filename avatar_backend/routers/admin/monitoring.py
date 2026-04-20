"""Monitoring sub-router: logs, pylog, costs, decisions, metrics (snapshots + SSE streams)."""
from __future__ import annotations

import asyncio
import structlog

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from avatar_backend.bootstrap.container import AppContainer, get_container

from .common import (
    _LOG_FILE,
    _get_session,
    _require_session,
)

_LOGGER = structlog.get_logger()
router = APIRouter()


# ── Live logs (SSE) ───────────────────────────────────────────────────────────

@router.get("/logs")
async def stream_logs(request: Request):
    if not _get_session(request):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)

    async def generate():
        if _LOG_FILE.exists():
            for line in _LOG_FILE.read_text().splitlines()[-100:]:
                yield f"data: {line}\n\n"

        pos = _LOG_FILE.stat().st_size if _LOG_FILE.exists() else 0
        while True:
            if await request.is_disconnected():
                break
            await asyncio.sleep(0.5)
            if not _LOG_FILE.exists():
                continue
            new_size = _LOG_FILE.stat().st_size
            if new_size > pos:
                with open(_LOG_FILE) as f:
                    f.seek(pos)
                    chunk = f.read()
                pos = new_size
                for line in chunk.splitlines():
                    if line.strip():
                        yield f"data: {line}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Python Logger (SSE + snapshot) ────────────────────────────────────────────

@router.get("/pylog")
async def get_pylog(request: Request, n: int = 500, level: str = "", container: AppContainer = Depends(get_container)):
    """Return recent server log entries as JSON (optionally filtered by level)."""
    if not _get_session(request):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    store = getattr(container, "log_store", None)
    entries = store.recent(n, level or None) if store else []
    return {"entries": entries}


@router.get("/pylog/stream")
async def stream_pylog(request: Request, container: AppContainer = Depends(get_container)):
    """SSE stream — pushes each new log entry as JSON."""
    if not _get_session(request):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)

    store = getattr(container, "log_store", None)
    if not store:
        return JSONResponse({"detail": "Log store not available"}, status_code=503)

    import json as _json

    async def generate():
        q = store.subscribe()
        try:
            for entry in store.recent(200):
                yield f"data: {_json.dumps(entry)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {_json.dumps(entry)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            store.unsubscribe(q)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── LLM Cost Log (SSE + snapshot) ────────────────────────────────────────────

@router.get("/costs")
async def get_costs(request: Request, container: AppContainer = Depends(get_container)):
    """Return recent LLM cost entries + session totals as JSON."""
    if not _get_session(request):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    log = getattr(container, "cost_log", None)
    db = getattr(container, "metrics_db", None)

    entries = log.recent(200) if log else []
    totals = log.totals() if log else {}

    if not entries and db:
        entries = db.recent_invocations(200)
        if entries:
            totals = _totals_from_entries(entries)

    return {"entries": entries, "totals": totals}


def _totals_from_entries(entries: list[dict]) -> dict:
    by_model: dict[str, dict] = {}
    total_input = 0
    total_output = 0
    total_cost = 0.0

    for e in entries:
        input_tokens = int(e.get("input_tokens", 0) or 0)
        output_tokens = int(e.get("output_tokens", 0) or 0)
        cost_usd = float(e.get("cost_usd", 0.0) or 0.0)
        total_input += input_tokens
        total_output += output_tokens
        total_cost += cost_usd
        key = f"{e.get('provider', '')}/{e.get('model', '')}"
        bucket = by_model.setdefault(key, {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "price_in": 0.0,
            "price_out": 0.0,
        })
        bucket["calls"] += 1
        bucket["input_tokens"] += input_tokens
        bucket["output_tokens"] += output_tokens
        bucket["cost_usd"] += cost_usd

    for bucket in by_model.values():
        bucket["cost_usd"] = round(bucket["cost_usd"], 6)

    return {
        "session_calls": len(entries),
        "session_input_tokens": total_input,
        "session_output_tokens": total_output,
        "session_cost_usd": round(total_cost, 6),
        "by_model": by_model,
    }


@router.get("/costs/stream")
async def stream_costs(request: Request, container: AppContainer = Depends(get_container)):
    """SSE stream — pushes each new LLM cost event as it happens."""
    if not _get_session(request):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    log = getattr(container, "cost_log", None)

    async def generate():
        import json as _json
        if not log:
            yield "data: {}\n\n"
            return
        q = log.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield f"data: {_json.dumps(entry)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            log.unsubscribe(q)

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── Cost history (persistent DB) ──────────────────────────────────────────────

@router.get("/costs/history")
async def get_cost_history(request: Request, period: str = "month", container: AppContainer = Depends(get_container)):
    """Return cost chart data filtered by period (day/week/month/year)."""
    if not _get_session(request):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    db = getattr(container, "metrics_db", None)
    if not db:
        return {"summary": {}, "by_day": [], "by_model": [], "monthly": []}

    period = period if period in ("day", "week", "month", "year") else "month"
    days_map = {"day": 1, "week": 7, "month": 30, "year": 365}

    summary  = db.cost_summary(period)
    by_day   = db.cost_by_day(days=days_map[period])
    by_model = db.cost_by_model(period)
    monthly  = db.monthly_totals(12)

    return {
        "summary":  summary,
        "by_day":   by_day,
        "by_model": by_model,
        "monthly":  monthly,
    }


# ── AI Decision Log (SSE + snapshot) ─────────────────────────────────────────

@router.get("/decisions")
async def get_decisions(request: Request, container: AppContainer = Depends(get_container)):
    """Return the last 200 AI decision events as JSON."""
    if not _get_session(request):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    log = getattr(container, "decision_log", None)
    return {"decisions": log.recent(200) if log else []}


@router.get("/decisions/stream")
async def stream_decisions(request: Request, container: AppContainer = Depends(get_container)):
    """SSE stream — pushes each new decision event as it happens."""
    if not _get_session(request):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    log = getattr(container, "decision_log", None)

    async def generate():
        import json as _json
        # Send backlog of recent decisions first
        if log:
            for entry in log.recent(50):
                yield f"data: {_json.dumps(entry)}\n\n"
        if not log:
            yield "data: {}\n\n"
            return
        q = log.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {_json.dumps(entry)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            log.unsubscribe(q)

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── System metrics ────────────────────────────────────────────────────────────

@router.get("/metrics")
async def get_metrics(request: Request, container: AppContainer = Depends(get_container)):
    """Return latest system sample + recent history."""
    if not _get_session(request):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    db       = getattr(container, "metrics_db", None)
    sys_svc  = getattr(container, "sys_metrics", None)
    latest   = sys_svc.latest() if sys_svc else (db.latest_sample() if db else None)
    history  = db.hourly_averages(24) if db else []
    return {"latest": latest, "history": history}


@router.get("/metrics/stream")
async def stream_metrics(request: Request, container: AppContainer = Depends(get_container)):
    """SSE stream — pushes a new system sample every 5 s."""
    if not _get_session(request):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    sys_svc = getattr(container, "sys_metrics", None)

    async def generate():
        import json as _json
        if not sys_svc:
            yield "data: {}\n\n"
            return
        latest = sys_svc.latest()
        if latest:
            yield f"data: {_json.dumps(latest)}\n\n"
        q = sys_svc.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    sample = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {_json.dumps(sample)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            sys_svc.unsubscribe(q)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/metrics/history")
async def get_metrics_history(request: Request, hours: int = 24, container: AppContainer = Depends(get_container)):
    """Return hourly averages for system metrics."""
    if not _get_session(request):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    db = getattr(container, "metrics_db", None)
    if not db:
        return {"averages": []}
    hours = min(max(hours, 1), 168)  # cap at 1 week
    return {"averages": db.hourly_averages(hours)}
