"""
Shared Home Assistant WebSocket manager.

Owns a single authenticated WebSocket connection to HA and multiplexes
state_changed events to all registered consumer callbacks. Replaces the
duplicate WS connections previously managed independently by
ProactiveService and SensorWatchService.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import structlog

try:
    import websockets  # type: ignore
except ImportError:
    websockets = None  # type: ignore

_LOGGER = structlog.get_logger()

# Type alias: consumers receive the raw HA event dict (the "event" key
# from the WS message, not the outer envelope).
Callback = Callable[[dict], None]


class HAWebSocketManager:
    """Single shared HA WebSocket with consumer dispatch."""

    def __init__(
        self,
        ha_url: str,
        ha_token: str,
        issue_autofix: Any | None = None,
    ) -> None:
        self._ha_url = ha_url
        self._ha_token = ha_token
        self._issue_autofix = issue_autofix
        self._consumers: dict[str, Callback] = {}
        self._task: asyncio.Task[None] | None = None
        self._connected = False
        self._state_mirror: dict[str, dict] = {}  # entity_id → full state dict

    # ── Consumer registration ─────────────────────────────────────────────

    def register(self, name: str, callback: Callback) -> None:
        self._consumers[name] = callback
        _LOGGER.info("ha_ws_manager.consumer_registered", name=name,
                     total=len(self._consumers))

    def unregister(self, name: str) -> None:
        self._consumers.pop(name, None)
        _LOGGER.info("ha_ws_manager.consumer_unregistered", name=name,
                     total=len(self._consumers))

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="ha_ws_manager")
        _LOGGER.info("ha_ws_manager.started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._connected = False
        _LOGGER.info("ha_ws_manager.stopped")

    async def restart(self) -> None:
        """Close current connection and reconnect. Callable by IssueAutoFixService."""
        _LOGGER.info("ha_ws_manager.restart_requested")
        await self.stop()
        await self.start()

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_all_states(self) -> list[dict]:
        """Return all cached entity states from the WebSocket mirror."""
        return list(self._state_mirror.values())

    def get_state(self, entity_id: str) -> dict | None:
        """Return a single cached entity state."""
        return self._state_mirror.get(entity_id)

    # ── Main reconnect loop ───────────────────────────────────────────────

    async def _run(self) -> None:
        backoff = 5
        while True:
            try:
                await self._ws_loop()
                backoff = 5
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connected = False
                _LOGGER.warning(
                    "ha_ws_manager.disconnected",
                    exc=str(exc),
                    retry_in_s=backoff,
                )
                if self._issue_autofix is not None:
                    await self._issue_autofix.report_issue(
                        "proactive_ws_disconnected",
                        source="ha_ws_manager._run",
                        summary="Shared HA WebSocket disconnected",
                        details={"exc": str(exc), "retry_in_s": backoff},
                    )
                    await self._issue_autofix.report_issue(
                        "sensor_watch_ws_disconnected",
                        source="ha_ws_manager._run",
                        summary="Shared HA WebSocket disconnected",
                        details={"exc": str(exc), "retry_in_s": backoff},
                    )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120)

    # ── WebSocket connection ──────────────────────────────────────────────

    async def _ws_loop(self) -> None:
        if websockets is None:
            raise RuntimeError("websockets library not installed")

        ws_url = (
            self._ha_url
            .replace("http://", "ws://")
            .replace("https://", "wss://")
            + "/api/websocket"
        )
        _LOGGER.info("ha_ws_manager.connecting", url=ws_url)

        async with websockets.connect(
            ws_url, ping_interval=30, ping_timeout=10, open_timeout=10,
            max_size=20 * 1024 * 1024,  # 20MB for get_states with many entities
        ) as ws:
            # ── Auth handshake ────────────────────────────────────────────
            msg = json.loads(await ws.recv())
            if msg.get("type") != "auth_required":
                raise RuntimeError(f"Expected auth_required, got {msg.get('type')}")

            await ws.send(json.dumps({
                "type": "auth",
                "access_token": self._ha_token,
            }))
            msg = json.loads(await ws.recv())
            if msg.get("type") != "auth_ok":
                raise RuntimeError(f"HA WebSocket auth failed: {msg}")

            _LOGGER.info("ha_ws_manager.authenticated")

            # ── Subscribe to state_changed ────────────────────────────────
            await ws.send(json.dumps({
                "id": 1,
                "type": "subscribe_events",
                "event_type": "state_changed",
            }))
            msg = json.loads(await ws.recv())
            if msg.get("type") != "result" or not msg.get("success"):
                raise RuntimeError(f"subscribe_events failed: {msg}")

            self._connected = True
            _LOGGER.info("ha_ws_manager.subscribed",
                         consumers=list(self._consumers.keys()))

            # Fetch initial state snapshot to populate the mirror
            await ws.send(json.dumps({"id": 2, "type": "get_states"}))
            states_msg = json.loads(await ws.recv())
            if states_msg.get("success") and isinstance(states_msg.get("result"), list):
                for s in states_msg["result"]:
                    eid = s.get("entity_id", "")
                    if eid:
                        self._state_mirror[eid] = s
                _LOGGER.info("ha_ws_manager.state_mirror_loaded", entities=len(self._state_mirror))

            # Resolve issues on successful connection
            if self._issue_autofix is not None:
                await self._issue_autofix.resolve_issue(
                    "proactive_ws_disconnected",
                    source="ha_ws_manager.ws_ready",
                )
                await self._issue_autofix.resolve_issue(
                    "sensor_watch_ws_disconnected",
                    source="ha_ws_manager.ws_ready",
                )

            # ── Dispatch loop ─────────────────────────────────────────────
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                self._dispatch(msg)

    # ── Event dispatch ────────────────────────────────────────────────────

    def _dispatch(self, msg: dict) -> None:
        """Dispatch a state_changed event to all registered consumers and update state mirror."""
        if msg.get("type") != "event":
            return
        # Update state mirror
        event = msg.get("event", {})
        event_data = event.get("data", {})
        new_state = event_data.get("new_state")
        if new_state and isinstance(new_state, dict):
            eid = new_state.get("entity_id", "")
            if eid:
                self._state_mirror[eid] = new_state
        # Pass the full message to consumers
        for name, callback in self._consumers.items():
            try:
                callback(msg)
            except Exception as exc:
                _LOGGER.warning(
                    "ha_ws_manager.consumer_error",
                    consumer=name,
                    exc=repr(exc),
                    exc_type=type(exc).__name__,
                )

    @staticmethod
    def compute_backoff(failure_count: int) -> float:
        """Compute reconnect delay: min(5 * 2^(i-1), 120) seconds.

        Exposed as a static method for testability.
        """
        if failure_count < 1:
            return 5.0
        return min(5.0 * (2 ** (failure_count - 1)), 120.0)
