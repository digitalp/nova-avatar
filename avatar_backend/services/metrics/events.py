"""Event history and canonical event store mixin for MetricsDB."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any


class EventsMixin:

    def insert_event_history(self, entry: dict[str, Any]) -> None:
        import json as _json

        ts = entry.get("ts") or datetime.now(timezone.utc).isoformat()
        data = dict(entry)
        payload = data.pop("data", {})
        payload = self._open_loop_service.enrich_event_data(
            ts=ts,
            status=str(data.get("status", "active")),
            data=payload,
            open_loop_note=(payload or {}).get("open_loop_note"),
            admin_note=(payload or {}).get("admin_note"),
        )
        with self._write_lock, self._write_conn as conn:
            conn.execute(
                """
                INSERT INTO event_history
                (ts, event_id, event_type, title, summary, status, event_source, camera_entity_id, data_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    data.get("event_id", ""),
                    data.get("event_type", ""),
                    data.get("title", ""),
                    data.get("summary", ""),
                    data.get("status", "active"),
                    data.get("event_source", ""),
                    data.get("camera_entity_id", ""),
                    _json.dumps(payload or {}),
                ),
            )

    def recent_event_history(self, n: int = 100) -> list[dict[str, Any]]:
        import json as _json

        sql = """
        SELECT ts, event_id, event_type, title, summary, status, event_source, camera_entity_id, data_json
        FROM event_history
        ORDER BY id DESC
        LIMIT ?
        """
        with self._conn() as conn:
            rows = conn.execute(sql, (n,)).fetchall()
        out: list[dict[str, Any]] = []
        for row in reversed(rows):
            entry = dict(row)
            entry["data"] = _json.loads(entry.pop("data_json", "{}") or "{}")
            out.append(entry)
        return out

    def update_event_history_status(
        self,
        event_id: str,
        status: str,
        open_loop_note: str | None = None,
        admin_note: str | None = None,
    ) -> bool:
        import json as _json

        if not event_id:
            return False
        with self._write_lock, self._write_conn as conn:
            rows = conn.execute(
                "SELECT id, data_json FROM event_history WHERE event_id = ?",
                (event_id,),
            ).fetchall()
            if not rows:
                return False
            for row in rows:
                data = _json.loads(row["data_json"] or "{}")
                data = self._open_loop_service.apply_status_transition(
                    status=status,
                    data=data,
                    open_loop_note=open_loop_note,
                    admin_note=admin_note,
                )
                conn.execute(
                    "UPDATE event_history SET status = ?, data_json = ? WHERE id = ?",
                    (status, _json.dumps(data), row["id"]),
                )
        return True

    def update_event_history_policy(
        self,
        event_id: str,
        *,
        reminder_sent: bool = False,
        escalation_level: str | None = None,
    ) -> bool:
        import json as _json

        if not event_id or (not reminder_sent and not escalation_level):
            return False
        with self._write_lock, self._write_conn as conn:
            rows = conn.execute(
                "SELECT id, data_json FROM event_history WHERE event_id = ?",
                (event_id,),
            ).fetchall()
            if not rows:
                return False
            for row in rows:
                data = _json.loads(row["data_json"] or "{}")
                data = self._open_loop_service.apply_policy_update(
                    data=data,
                    reminder_sent=reminder_sent,
                    escalation_level=escalation_level,
                )
                conn.execute(
                    "UPDATE event_history SET data_json = ? WHERE id = ?",
                    (_json.dumps(data), row["id"]),
                )
        return True

    # ── Canonical event store ───────────────────────────────────────────────

    def insert_event_record(self, entry: dict[str, Any]) -> None:
        import json as _json

        event_id = str(entry.get("event_id") or "").strip()
        if not event_id:
            raise ValueError("event_id is required")
        created_at = str(entry.get("created_at") or datetime.now(timezone.utc).isoformat())
        payload = dict(entry.get("data") or {})
        with self._write_lock, self._write_conn as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO events
                (event_id, event_type, source, room, camera_entity_id, severity, summary, details,
                 confidence, status, created_at, expires_at, linked_session_id, data_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    entry.get("event_type", ""),
                    entry.get("source", ""),
                    entry.get("room", ""),
                    entry.get("camera_entity_id", ""),
                    entry.get("severity", "normal"),
                    entry.get("summary", ""),
                    entry.get("details", ""),
                    entry.get("confidence"),
                    entry.get("status", "active"),
                    created_at,
                    entry.get("expires_at", ""),
                    entry.get("linked_session_id", ""),
                    _json.dumps(payload),
                ),
            )

    def get_event_record(self, event_id: str) -> dict[str, Any] | None:
        import json as _json

        if not event_id:
            return None
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT event_id, event_type, source, room, camera_entity_id, severity, summary, details,
                       confidence, status, created_at, expires_at, linked_session_id, data_json
                FROM events
                WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
        if not row:
            return None
        entry = dict(row)
        entry["data"] = _json.loads(entry.pop("data_json", "{}") or "{}")
        return entry

    def list_event_records(
        self,
        *,
        limit: int = 100,
        event_type: str | None = None,
        status: str | None = None,
        source: str | None = None,
        camera_entity_id: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
    ) -> list[dict[str, Any]]:
        import json as _json

        clauses: list[str] = []
        args: list[Any] = []
        if event_type:
            clauses.append("event_type = ?")
            args.append(event_type)
        if status:
            clauses.append("status = ?")
            args.append(status)
        if source:
            clauses.append("source = ?")
            args.append(source)
        if camera_entity_id:
            clauses.append("camera_entity_id = ?")
            args.append(camera_entity_id)
        if created_after:
            clauses.append("created_at >= ?")
            args.append(created_after)
        if created_before:
            clauses.append("created_at <= ?")
            args.append(created_before)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        args.append(max(1, min(limit, 500)))
        sql = f"""
        SELECT event_id, event_type, source, room, camera_entity_id, severity, summary, details,
               confidence, status, created_at, expires_at, linked_session_id, data_json
        FROM events
        {where_sql}
        ORDER BY created_at DESC
        LIMIT ?
        """
        with self._conn() as conn:
            rows = conn.execute(sql, tuple(args)).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            entry = dict(row)
            entry["data"] = _json.loads(entry.pop("data_json", "{}") or "{}")
            out.append(entry)
        return out

    def update_event_record_status(
        self,
        event_id: str,
        *,
        status: str,
        open_loop_note: str | None = None,
        admin_note: str | None = None,
        reminder_sent: bool = False,
        escalation_level: str | None = None,
    ) -> bool:
        import json as _json

        if not event_id:
            return False
        with self._write_lock, self._write_conn as conn:
            row = conn.execute(
                "SELECT data_json FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if not row:
                return False
            data = _json.loads(row["data_json"] or "{}")
            created_row = conn.execute(
                "SELECT created_at FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            fallback_ts = str(created_row["created_at"] if created_row else "") or datetime.now(timezone.utc).isoformat()
            data.setdefault("open_loop_started_ts", fallback_ts)
            data = self._open_loop_service.apply_status_transition(
                status=status,
                data=data,
                open_loop_note=open_loop_note,
                admin_note=admin_note,
            )
            data = self._open_loop_service.apply_policy_update(
                data=data,
                reminder_sent=reminder_sent,
                escalation_level=escalation_level,
            )
            conn.execute(
                "UPDATE events SET status = ?, data_json = ? WHERE event_id = ?",
                (status, _json.dumps(data), event_id),
            )
        return True

    def insert_event_action(
        self,
        *,
        event_id: str,
        action_id: str,
        action_type: str,
        status: str = "completed",
        result: dict[str, Any] | None = None,
        ts: str | None = None,
    ) -> None:
        import json as _json

        with self._write_lock, self._write_conn as conn:
            conn.execute(
                """
                INSERT INTO event_actions (ts, event_id, action_id, action_type, status, result_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    ts or datetime.now(timezone.utc).isoformat(),
                    event_id,
                    action_id,
                    action_type,
                    status,
                    _json.dumps(result or {}),
                ),
            )

    def list_event_actions(self, event_id: str) -> list[dict[str, Any]]:
        import json as _json

        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT ts, event_id, action_id, action_type, status, result_json
                FROM event_actions
                WHERE event_id = ?
                ORDER BY id ASC
                """,
                (event_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            entry = dict(row)
            entry["result"] = _json.loads(entry.pop("result_json", "{}") or "{}")
            out.append(entry)
        return out

    def insert_event_media(
        self,
        *,
        event_id: str,
        media_type: str,
        url: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        import json as _json

        with self._write_lock, self._write_conn as conn:
            conn.execute(
                """
                INSERT INTO event_media (event_id, media_type, url, metadata_json)
                VALUES (?, ?, ?, ?)
                """,
                (event_id, media_type, url, _json.dumps(metadata or {})),
            )

    def list_event_media(self, event_id: str) -> list[dict[str, Any]]:
        import json as _json

        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT event_id, media_type, url, metadata_json
                FROM event_media
                WHERE event_id = ?
                ORDER BY id ASC
                """,
                (event_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            entry = dict(row)
            entry["metadata"] = _json.loads(entry.pop("metadata_json", "{}") or "{}")
            out.append(entry)
        return out

    def upsert_conversation_session(
        self,
        *,
        session_id: str,
        surface: str = "",
        linked_event_id: str = "",
        metadata: dict[str, Any] | None = None,
        now_iso: str | None = None,
    ) -> None:
        import json as _json

        ts = now_iso or datetime.now(timezone.utc).isoformat()
        with self._write_lock, self._write_conn as conn:
            conn.execute(
                """
                INSERT INTO conversation_sessions
                (session_id, started_at, updated_at, surface, linked_event_id, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    surface=excluded.surface,
                    linked_event_id=excluded.linked_event_id,
                    metadata_json=excluded.metadata_json
                """,
                (session_id, ts, ts, surface, linked_event_id, _json.dumps(metadata or {})),
            )

    def insert_conversation_turn_summary(
        self,
        *,
        session_id: str,
        role: str,
        summary: str,
        event_id: str = "",
        metadata: dict[str, Any] | None = None,
        ts: str | None = None,
    ) -> None:
        import json as _json

        with self._write_lock, self._write_conn as conn:
            conn.execute(
                """
                INSERT INTO conversation_turn_summaries
                (session_id, ts, role, summary, event_id, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    ts or datetime.now(timezone.utc).isoformat(),
                    role,
                    summary,
                    event_id,
                    _json.dumps(metadata or {}),
                ),
            )
