"""
Avatar state WebSocket endpoint — /ws/avatar

Read-only WebSocket that streams avatar state change events.
Used by the Lovelace avatar card and any other UI component that
wants to reflect the current avatar state without being part of
the voice pipeline.

Protocol
--------
Server → Client (text, JSON):
  {"type": "avatar_state", "state": "<idle|listening|thinking|speaking|alert|error>"}
  {"type": "pong"}

Client → Server (text, JSON):
  {"type": "ping"}   (keepalive — server replies with pong)

Authentication:
  ?api_key=<key>  query parameter
"""
from __future__ import annotations
import json
import logging
import uuid

import structlog
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from avatar_backend.middleware.auth import verify_api_key_ws
from avatar_backend.services.event_service import publish_visual_event
from avatar_backend.services.ws_manager import ConnectionManager

_LOGGER = structlog.get_logger()

router = APIRouter()


@router.websocket("/ws/avatar")
async def avatar_state_websocket(
    ws: WebSocket,
    _: None = Depends(verify_api_key_ws),
):
    """
    State-only WebSocket for avatar UI components.
    Joins the broadcast group; state updates arrive automatically
    whenever the voice pipeline changes state.
    """
    ws_mgr: ConnectionManager = ws.app.state.ws_manager

    await ws_mgr.connect(ws)
    surface_state = await ws.app.state.surface_state_service.get_snapshot()
    await ws.send_text(json.dumps({"type": "avatar_state", "state": surface_state["avatar_state"]}))
    await ws.send_text(json.dumps({"type": "surface_state", **surface_state}))

    try:
        while True:
            msg = await ws.receive()

            if msg["type"] == "websocket.disconnect":
                break

            if msg.get("text"):
                try:
                    data = json.loads(msg["text"])
                    if data.get("type") == "ping":
                        await ws.send_text(json.dumps({"type": "pong"}))
                    elif data.get("type") == "surface_action":
                        surface_state = ws.app.state.surface_state_service
                        action = str(data.get("action") or "")
                        if action == "dismiss_active_event":
                            await surface_state.dismiss_active_event(ws_mgr)
                            await ws.send_text(json.dumps({"type": "surface_action_ack", "action": action}))
                        elif action == "acknowledge_active_event":
                            await surface_state.acknowledge_active_event(ws_mgr)
                            await ws.send_text(json.dumps({"type": "surface_action_ack", "action": action}))
                        elif action == "resolve_active_event":
                            await surface_state.resolve_active_event(ws_mgr)
                            await ws.send_text(json.dumps({"type": "surface_action_ack", "action": action}))
                        elif action == "snooze_active_event":
                            await surface_state.snooze_active_event(ws_mgr)
                            await ws.send_text(json.dumps({"type": "surface_action_ack", "action": action}))
                        elif action == "dismiss_recent_event":
                            event_id = str(data.get("event_id") or "").strip()
                            ok = await surface_state.dismiss_recent_event(ws_mgr, event_id)
                            await ws.send_text(json.dumps({
                                "type": "surface_action_ack",
                                "action": action,
                                "event_id": event_id,
                                "ok": ok,
                            }))
                        elif action == "acknowledge_recent_event":
                            event_id = str(data.get("event_id") or "").strip()
                            ok = await surface_state.acknowledge_recent_event(ws_mgr, event_id)
                            await ws.send_text(json.dumps({
                                "type": "surface_action_ack",
                                "action": action,
                                "event_id": event_id,
                                "ok": ok,
                            }))
                        elif action == "resolve_recent_event":
                            event_id = str(data.get("event_id") or "").strip()
                            ok = await surface_state.resolve_recent_event(ws_mgr, event_id)
                            await ws.send_text(json.dumps({
                                "type": "surface_action_ack",
                                "action": action,
                                "event_id": event_id,
                                "ok": ok,
                            }))
                        elif action == "snooze_recent_event":
                            event_id = str(data.get("event_id") or "").strip()
                            ok = await surface_state.snooze_recent_event(ws_mgr, event_id)
                            await ws.send_text(json.dumps({
                                "type": "surface_action_ack",
                                "action": action,
                                "event_id": event_id,
                                "ok": ok,
                            }))
                        elif action == "activate_recent_event":
                            event_id = str(data.get("event_id") or "").strip()
                            ok = await surface_state.activate_recent_event(ws_mgr, event_id)
                            await ws.send_text(json.dumps({
                                "type": "surface_action_ack",
                                "action": action,
                                "event_id": event_id,
                                "ok": ok,
                            }))
                        elif action == "show_related_camera":
                            source_event_id = str(data.get("event_id") or "").strip()
                            target_camera_entity_id = str(data.get("target_camera_entity_id") or "").strip()
                            target_event = str(data.get("target_event") or "related_camera").strip() or "related_camera"
                            target_title = str(data.get("target_title") or "Related camera").strip() or "Related camera"
                            target_message = str(data.get("target_message") or "Related live view").strip() or "Related live view"
                            if target_camera_entity_id:
                                ha = ws.app.state.ha_proxy
                                event_service = getattr(ws.app.state, "event_service", None)
                                surface_state = ws.app.state.surface_state_service
                                resolved_camera = ha.resolve_camera_entity(target_camera_entity_id)
                                event_id = uuid.uuid4().hex
                                await publish_visual_event(
                                    app=ws.app,
                                    ws_mgr=ws_mgr,
                                    event_service=event_service,
                                    surface_state=surface_state,
                                    event_id=event_id,
                                    event_type=target_event,
                                    title=target_title,
                                    message=target_message,
                                    camera_entity_id=resolved_camera,
                                    event_context={
                                        "source": "surface_action",
                                        "related_to_event_id": source_event_id,
                                    },
                                    expires_in_ms=45000,
                                )
                                await ws.send_text(json.dumps({
                                    "type": "surface_action_ack",
                                    "action": action,
                                    "event_id": source_event_id,
                                    "ok": True,
                                    "opened_event_id": event_id,
                                }))
                            else:
                                await ws.send_text(json.dumps({
                                    "type": "surface_action_ack",
                                    "action": action,
                                    "event_id": source_event_id,
                                    "ok": False,
                                }))
                except Exception:
                    pass

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        _LOGGER.warning("avatar_ws.error", exc=str(exc))
    finally:
        await ws_mgr.disconnect(ws)
