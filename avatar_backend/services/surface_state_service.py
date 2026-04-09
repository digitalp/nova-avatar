from __future__ import annotations

import asyncio
import time
from typing import Any

from avatar_backend.services.ws_manager import ConnectionManager


class SurfaceStateService:
    """Compatibility-first surface state registry for avatar and voice clients."""
    _SNOOZE_SECONDS = 30 * 60

    def __init__(self, *, max_recent_events: int = 8) -> None:
        self._max_recent_events = max_recent_events
        self._avatar_state = "idle"
        self._active_event: dict[str, Any] | None = None
        self._recent_events: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def set_avatar_state(self, ws_mgr: ConnectionManager, state: str) -> None:
        async with self._lock:
            self._avatar_state = state
            snapshot = self._snapshot_unlocked()
        await ws_mgr.broadcast_json({"type": "avatar_state", "state": state})
        await self._broadcast_snapshot(ws_mgr, snapshot)

    async def record_visual_event(self, ws_mgr: ConnectionManager, event_payload: dict[str, Any]) -> None:
        event_record = {
            "event_id": event_payload.get("event_id"),
            "event": event_payload.get("event"),
            "title": event_payload.get("title"),
            "message": event_payload.get("message"),
            "camera_entity_id": event_payload.get("camera_entity_id"),
            "image_urls": list(event_payload.get("image_urls") or []),
            "expires_in_ms": event_payload.get("expires_in_ms"),
            "status": "active",
            "open_loop_note": str(event_payload.get("open_loop_note") or "Needs attention"),
            "ts": time.time(),
        }
        async with self._lock:
            self._active_event = event_record
            self._recent_events = [event_record] + [
                item for item in self._recent_events
                if item.get("event_id") != event_record.get("event_id")
            ]
            self._recent_events = self._recent_events[: self._max_recent_events]
            snapshot = self._snapshot_unlocked()
        await self._broadcast_snapshot(ws_mgr, snapshot)

    async def get_snapshot(self) -> dict[str, Any]:
        async with self._lock:
            return self._snapshot_unlocked()

    async def dismiss_active_event(self, ws_mgr: ConnectionManager) -> None:
        async with self._lock:
            if self._active_event and self._active_event.get("event_id"):
                active_id = self._active_event["event_id"]
                for item in self._recent_events:
                    if item.get("event_id") == active_id:
                        item["status"] = "dismissed"
                        item["open_loop_note"] = "Hidden for now"
            self._active_event = None
            snapshot = self._snapshot_unlocked()
        await self._broadcast_snapshot(ws_mgr, snapshot)

    async def acknowledge_active_event(self, ws_mgr: ConnectionManager) -> None:
        async with self._lock:
            if self._active_event and self._active_event.get("event_id"):
                active_id = self._active_event["event_id"]
                for item in self._recent_events:
                    if item.get("event_id") == active_id:
                        item["status"] = "acknowledged"
                        item["open_loop_note"] = "Seen by user"
                        self._active_event = dict(item)
                        break
            snapshot = self._snapshot_unlocked()
        await self._broadcast_snapshot(ws_mgr, snapshot)

    async def resolve_active_event(self, ws_mgr: ConnectionManager) -> None:
        async with self._lock:
            if self._active_event and self._active_event.get("event_id"):
                active_id = self._active_event["event_id"]
                for item in self._recent_events:
                    if item.get("event_id") == active_id:
                        item["status"] = "resolved"
                        item["open_loop_note"] = "Closed out"
                        break
            self._active_event = None
            snapshot = self._snapshot_unlocked()
        await self._broadcast_snapshot(ws_mgr, snapshot)

    async def snooze_active_event(self, ws_mgr: ConnectionManager) -> None:
        async with self._lock:
            if self._active_event and self._active_event.get("event_id"):
                active_id = self._active_event["event_id"]
                snoozed_until = time.time() + self._SNOOZE_SECONDS
                for item in self._recent_events:
                    if item.get("event_id") == active_id:
                        item["status"] = "snoozed"
                        item["snoozed_until_ts"] = snoozed_until
                        item["open_loop_note"] = "Snoozed for 30 minutes"
                        break
            self._active_event = None
            snapshot = self._snapshot_unlocked()
        await self._broadcast_snapshot(ws_mgr, snapshot)

    async def dismiss_recent_event(self, ws_mgr: ConnectionManager, event_id: str) -> bool:
        async with self._lock:
            match = next((item for item in self._recent_events if item.get("event_id") == event_id), None)
            if not match:
                return False
            match["status"] = "dismissed"
            match["open_loop_note"] = "Hidden for now"
            if self._active_event and self._active_event.get("event_id") == event_id:
                self._active_event = None
            snapshot = self._snapshot_unlocked()
        await self._broadcast_snapshot(ws_mgr, snapshot)
        return True

    async def acknowledge_recent_event(self, ws_mgr: ConnectionManager, event_id: str) -> bool:
        async with self._lock:
            match = next((item for item in self._recent_events if item.get("event_id") == event_id), None)
            if not match:
                return False
            match["status"] = "acknowledged"
            match["open_loop_note"] = "Seen by user"
            if self._active_event and self._active_event.get("event_id") == event_id:
                self._active_event = dict(match)
            snapshot = self._snapshot_unlocked()
        await self._broadcast_snapshot(ws_mgr, snapshot)
        return True

    async def resolve_recent_event(self, ws_mgr: ConnectionManager, event_id: str) -> bool:
        async with self._lock:
            match = next((item for item in self._recent_events if item.get("event_id") == event_id), None)
            if not match:
                return False
            match["status"] = "resolved"
            match["open_loop_note"] = "Closed out"
            if self._active_event and self._active_event.get("event_id") == event_id:
                self._active_event = None
            snapshot = self._snapshot_unlocked()
        await self._broadcast_snapshot(ws_mgr, snapshot)
        return True

    async def snooze_recent_event(self, ws_mgr: ConnectionManager, event_id: str) -> bool:
        async with self._lock:
            match = next((item for item in self._recent_events if item.get("event_id") == event_id), None)
            if not match:
                return False
            match["status"] = "snoozed"
            match["snoozed_until_ts"] = time.time() + self._SNOOZE_SECONDS
            match["open_loop_note"] = "Snoozed for 30 minutes"
            if self._active_event and self._active_event.get("event_id") == event_id:
                self._active_event = None
            snapshot = self._snapshot_unlocked()
        await self._broadcast_snapshot(ws_mgr, snapshot)
        return True

    async def activate_recent_event(self, ws_mgr: ConnectionManager, event_id: str) -> bool:
        async with self._lock:
            match = next((item for item in self._recent_events if item.get("event_id") == event_id), None)
            if not match:
                return False
            match["status"] = "active"
            match["open_loop_note"] = "Needs attention"
            match.pop("snoozed_until_ts", None)
            self._active_event = dict(match)
            snapshot = self._snapshot_unlocked()
        await self._broadcast_snapshot(ws_mgr, snapshot)
        return True

    async def _broadcast_snapshot(self, ws_mgr: ConnectionManager, snapshot: dict[str, Any]) -> None:
        payload = {"type": "surface_state", **snapshot}
        await ws_mgr.broadcast_json(payload)
        await ws_mgr.broadcast_to_voice_json(payload)

    def _snapshot_unlocked(self) -> dict[str, Any]:
        return {
            "avatar_state": self._avatar_state,
            "active_event": self._serialize_event(self._active_event, is_active=True) if self._active_event else None,
            "recent_events": [self._serialize_event(item, is_active=False) for item in self._recent_events],
        }

    def _serialize_event(self, event_record: dict[str, Any], *, is_active: bool) -> dict[str, Any]:
        payload = dict(event_record)
        payload["suggested_actions"] = self._build_suggested_actions(payload, is_active=is_active)
        return payload

    def _build_suggested_actions(self, event_record: dict[str, Any], *, is_active: bool) -> list[dict[str, Any]]:
        status = str(event_record.get("status") or "active")
        has_event_id = bool(event_record.get("event_id"))
        actions: list[dict[str, Any]] = []
        if not has_event_id:
            return actions
        if is_active:
            actions.extend(self._followup_actions(event_record))
            if status not in {"acknowledged", "resolved"}:
                actions.append(self._action(
                    "acknowledge_active_event",
                    "Acknowledge",
                    tone="warn",
                    requires_confirmation=True,
                    confirm_text="Acknowledge this event?",
                ))
            if status != "snoozed":
                actions.append(self._action(
                    "snooze_active_event",
                    "Snooze 30m",
                    tone="quiet",
                    requires_confirmation=True,
                    confirm_text="Snooze this event for 30 minutes?",
                ))
            if status != "dismissed":
                actions.append(self._action(
                    "dismiss_active_event",
                    "Dismiss",
                    tone="quiet",
                    requires_confirmation=True,
                    confirm_text="Hide this event for now?",
                ))
            if status != "resolved":
                actions.append(self._action(
                    "resolve_active_event",
                    "Resolve",
                    tone="success",
                    requires_confirmation=True,
                    confirm_text="Mark this event as resolved?",
                ))
            return actions

        if status in {"dismissed", "resolved", "snoozed"}:
            actions.append(self._action(
                "activate_recent_event",
                "Unsnooze" if status == "snoozed" else "Reopen",
                tone="info",
                requires_confirmation=False,
            ))
        else:
            actions.extend(self._followup_actions(event_record))
            if status != "acknowledged":
                actions.append(self._action(
                    "acknowledge_recent_event",
                    "Acknowledge",
                    tone="warn",
                    requires_confirmation=True,
                    confirm_text="Acknowledge this event?",
                ))
            if status != "snoozed":
                actions.append(self._action(
                    "snooze_recent_event",
                    "Snooze 30m",
                    tone="quiet",
                    requires_confirmation=True,
                    confirm_text="Snooze this event for 30 minutes?",
                ))
            if status != "dismissed":
                actions.append(self._action(
                    "dismiss_recent_event",
                    "Dismiss",
                    tone="quiet",
                    requires_confirmation=True,
                    confirm_text="Hide this event for now?",
                ))
            if status != "resolved":
                actions.append(self._action(
                    "resolve_recent_event",
                    "Resolve",
                    tone="success",
                    requires_confirmation=True,
                    confirm_text="Mark this event as resolved?",
                ))
        return actions

    @staticmethod
    def _action(
        action: str,
        label: str,
        *,
        tone: str,
        requires_confirmation: bool,
        confirm_text: str | None = None,
        followup_prompt: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "action": action,
            "label": label,
            "tone": tone,
            "requires_confirmation": requires_confirmation,
        }
        if confirm_text:
            payload["confirm_text"] = confirm_text
        if followup_prompt:
            payload["followup_prompt"] = followup_prompt
        if extra:
            payload.update(extra)
        return payload

    def _followup_actions(self, event_record: dict[str, Any]) -> list[dict[str, Any]]:
        text = " ".join(
            str(event_record.get(key) or "")
            for key in ("event", "title", "message", "open_loop_note")
        ).lower()
        label = "Ask about this"
        prompt = "Focus on the most relevant detail in this event before answering the user's question."
        actions: list[dict[str, Any]] = []
        if "doorbell" in text or "visitor" in text:
            label = "Ask who is there"
            prompt = "Focus on who is at the door, whether they appear familiar, and whether this looks like a delivery or visit."
            actions.append(self._action(
                "show_related_camera",
                "Show driveway too",
                tone="info",
                requires_confirmation=False,
                extra={
                    "target_camera_entity_id": "camera.outdoor_2",
                    "target_event": "related_camera",
                    "target_title": "Driveway",
                    "target_message": "Driveway live view",
                },
            ))
            actions.append(self._action(
                "ask_about_event",
                "Ask if it is a delivery",
                tone="info",
                requires_confirmation=False,
                followup_prompt="Focus on whether this event appears to be a package delivery, courier stop, or personal visitor.",
            ))
        elif "package" in text or "parcel" in text:
            label = "Ask about the delivery"
            prompt = "Focus on what was delivered, where the package was left, and whether it appears exposed or still outside."
            actions.append(self._action(
                "ask_about_event",
                "Ask where the package is",
                tone="info",
                requires_confirmation=False,
                followup_prompt="Focus on where the package or parcel was placed and whether it looks reachable, hidden, or exposed.",
            ))
        elif "driveway" in text or "vehicle" in text or "car" in text:
            label = "Ask about the vehicle"
            prompt = "Focus on the vehicle, what it is doing, and whether the arrival looks expected or unusual."
            actions.append(self._action(
                "show_related_camera",
                "Show doorbell too",
                tone="info",
                requires_confirmation=False,
                extra={
                    "target_camera_entity_id": "camera.doorbell",
                    "target_event": "related_camera",
                    "target_title": "Doorbell",
                    "target_message": "Front door live view",
                },
            ))
            actions.append(self._action(
                "ask_about_event",
                "Ask if it seems expected",
                tone="info",
                requires_confirmation=False,
                followup_prompt="Focus on whether the vehicle activity looks routine, expected, or worth attention.",
            ))
        elif "motion" in text or "outside" in text or "garden" in text:
            label = "Ask what moved"
            prompt = "Focus on what caused the motion, whether a person, animal, or vehicle is visible, and whether it needs attention."
            actions.append(self._action(
                "ask_about_event",
                "Ask if it matters",
                tone="info",
                requires_confirmation=False,
                followup_prompt="Focus on whether this motion looks meaningful, unusual, or worth following up on.",
            ))
        actions.insert(0, self._action(
            "ask_about_event",
            label,
            tone="info",
            requires_confirmation=False,
            followup_prompt=prompt,
        ))
        return actions
