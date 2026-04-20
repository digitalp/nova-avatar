"""Background tasks — helper coroutines and scheduling."""

from __future__ import annotations

import asyncio

import structlog
from fastapi import FastAPI


async def _session_cleanup_loop(sm, interval: int = 300) -> None:
    while True:
        await asyncio.sleep(interval)
        await sm.cleanup_expired()


async def _clip_cleanup_loop(svc, interval_h: int = 24) -> None:
    await asyncio.sleep(300)
    while True:
        try:
            await svc.run_cleanup()
        except Exception as exc:
            structlog.get_logger().warning("clip_cleanup.error", exc=str(exc))
        await asyncio.sleep(interval_h * 3600)


async def _audit_cleanup_loop(db, interval_h: int = 24) -> None:
    await asyncio.sleep(600)
    while True:
        try:
            db.cleanup_old_audits(retention_days=30)
        except Exception:
            pass
        await asyncio.sleep(interval_h * 3600)


async def _backfill_thumbs(motion_clip_service) -> None:
    await asyncio.sleep(30)
    try:
        result = await motion_clip_service.backfill_thumbnails()
        if result.get("generated", 0) > 0:
            structlog.get_logger().info("thumbnail_backfill.done", **result)
    except Exception as exc:
        structlog.get_logger().debug("thumbnail_backfill.skipped", exc=str(exc))


async def _restart_fully_kiosk_after_startup(app: FastAPI, delay_s: float = 5.0) -> None:
    await asyncio.sleep(delay_s)
    logger = structlog.get_logger()
    ws_mgr = getattr(app.state, "ws_manager", None)
    ha = getattr(app.state, "ha_proxy", None)
    from avatar_backend.services.home_runtime import load_home_runtime_config
    _rt = load_home_runtime_config()
    kiosk_entity = getattr(_rt, "kiosk_restart_entity", "") or ""
    if ha is not None and kiosk_entity:
        try:
            domain, _ = kiosk_entity.split(".", 1)
            result = await ha.call_service(domain, "press", kiosk_entity)
        except Exception as exc:
            logger.warning("avatar_backend.kiosk_restart_failed", entity_id=kiosk_entity, error=str(exc))
        else:
            if result.success:
                logger.info("avatar_backend.kiosk_restart_requested", entity_id=kiosk_entity)
    if ws_mgr is not None:
        payload = {"type": "server_restarted"}
        await ws_mgr.broadcast_json(payload)
        await ws_mgr.broadcast_to_voice_json(payload)
        logger.info("avatar_backend.restart_signal_broadcast")


def schedule_background_tasks(app: FastAPI, container) -> None:
    """Schedule all background asyncio tasks. Called after service creation."""
    container._background_tasks.append(asyncio.create_task(_restart_fully_kiosk_after_startup(app), name="kiosk_restart"))
    container._background_tasks.append(asyncio.create_task(_session_cleanup_loop(container.session_manager), name="session_cleanup"))
    container._background_tasks.append(asyncio.create_task(_clip_cleanup_loop(container.motion_clip_service), name="clip_cleanup"))
    container._background_tasks.append(asyncio.create_task(_audit_cleanup_loop(container.metrics_db), name="audit_cleanup"))
    container._background_tasks.append(asyncio.create_task(_backfill_thumbs(container.motion_clip_service), name="thumb_backfill"))
