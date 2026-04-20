"""Logging, decisions, health checks, and conversation audit mixin for MetricsDB."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta


class LogsMixin:

    # ── Decision events ──────────────────────────────────────────────────────

    def insert_decision(self, entry: dict) -> None:
        import json as _json
        data = dict(entry)
        ts   = data.pop("ts", datetime.now().strftime("%H:%M:%S"))
        kind = data.pop("kind", "unknown")
        full_ts = datetime.now().isoformat()
        with self._write_lock, self._write_conn as conn:
            conn.execute(
                "INSERT INTO decision_events (ts, kind, data) VALUES (?, ?, ?)",
                (full_ts, kind, _json.dumps(data)),
            )

    def recent_decisions(self, n: int = 200) -> list[dict]:
        import json as _json
        sql = ("SELECT ts, kind, data FROM decision_events "
               "ORDER BY id DESC LIMIT ?")
        with self._conn() as conn:
            rows = conn.execute(sql, (n,)).fetchall()
        out = []
        for r in reversed(rows):
            entry = _json.loads(r["data"])
            entry["ts"]   = r["ts"][11:19]   # HH:MM:SS from ISO timestamp
            entry["kind"] = r["kind"]
            out.append(entry)
        return out

    # ── Server logs ──────────────────────────────────────────────────────────

    def insert_log(self, entry: dict) -> None:
        import json as _json
        data = dict(entry)
        ts     = data.pop("ts", datetime.now(timezone.utc).isoformat())
        level  = data.pop("level", "info")
        event  = data.pop("event", "")
        logger = data.pop("logger", "")
        with self._write_lock, self._write_conn as conn:
            conn.execute(
                "INSERT INTO server_logs (ts, level, event, logger, data) VALUES (?, ?, ?, ?, ?)",
                (ts, level, event, logger, _json.dumps(data)),
            )

    def recent_logs(self, n: int = 500, level: str | None = None) -> list[dict]:
        import json as _json
        if level:
            sql  = "SELECT ts, level, event, logger, data FROM server_logs WHERE level=? ORDER BY id DESC LIMIT ?"
            args = (level, n)
        else:
            sql  = "SELECT ts, level, event, logger, data FROM server_logs ORDER BY id DESC LIMIT ?"
            args = (n,)
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        out = []
        for r in reversed(rows):
            extra = _json.loads(r["data"])
            entry = {"ts": r["ts"][11:19], "level": r["level"], "event": r["event"], "logger": r["logger"]}
            entry.update(extra)
            out.append(entry)
        return out

    def purge_old_logs(self, keep_days: int = 7) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
        with self._write_lock, self._write_conn as conn:
            cur = conn.execute("DELETE FROM server_logs WHERE ts < ?", (cutoff,))
            return cur.rowcount

    def purge_old_decisions(self, keep_days: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
        with self._write_lock, self._write_conn as conn:
            cur = conn.execute("DELETE FROM decision_events WHERE ts < ?", (cutoff,))
            return cur.rowcount

    def purge_old_samples(self, keep_days: int = 7) -> int:
        """Delete system samples older than keep_days. Returns rows deleted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
        with self._write_lock, self._write_conn as conn:
            cur = conn.execute("DELETE FROM system_samples WHERE ts < ?", (cutoff,))
            return cur.rowcount

    # ── Health check history ───────────────────────────────────────────────

    _HEALTH_CHECK_CAP = 2880  # ~48 h at 1 check/min

    def insert_health_check(self, component: str, status: str) -> None:
        """Persist a single health-check probe result and auto-prune old rows."""
        ts = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self._write_conn as conn:
            conn.execute(
                "INSERT INTO health_checks (ts, component, status) VALUES (?, ?, ?)",
                (ts, component, status),
            )
            # Auto-prune: keep only the most recent _HEALTH_CHECK_CAP rows per component
            conn.execute(
                """DELETE FROM health_checks
                   WHERE component = ?
                     AND id NOT IN (
                         SELECT id FROM health_checks
                         WHERE component = ?
                         ORDER BY ts DESC
                         LIMIT ?
                     )""",
                (component, component, self._HEALTH_CHECK_CAP),
            )

    def get_health_history(
        self,
        *,
        component: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict]:
        """Return health-check rows, optionally filtered by component and time range."""
        clauses: list[str] = []
        params: list[str] = []
        if component:
            clauses.append("component = ?")
            params.append(component)
        if since:
            clauses.append("ts >= ?")
            params.append(since)
        if until:
            clauses.append("ts <= ?")
            params.append(until)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT id, ts, component, status FROM health_checks{where} ORDER BY ts DESC"
        with self._write_lock, self._write_conn as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── Conversation audit trail ──────────────────────────────────────────

    def insert_conversation_audit(self, record: dict) -> None:
        import json as _json
        ts = record.get("ts") or datetime.now(timezone.utc).isoformat()
        with self._write_lock, self._write_conn as conn:
            conn.execute(
                """INSERT INTO conversation_audit
                   (ts, session_id, user_text, context_summary, llm_response,
                    tool_calls_json, final_reply, processing_ms, model)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ts,
                    record.get("session_id", ""),
                    record.get("user_text", ""),
                    record.get("context_summary", ""),
                    record.get("llm_response", ""),
                    _json.dumps(record.get("tool_calls", [])),
                    record.get("final_reply", ""),
                    int(record.get("processing_ms", 0)),
                    record.get("model", ""),
                ),
            )

    def list_conversation_audits(
        self, limit: int = 100, session_id: str | None = None
    ) -> list[dict]:
        import json as _json
        if session_id:
            sql = """SELECT * FROM conversation_audit
                     WHERE session_id = ? ORDER BY id DESC LIMIT ?"""
            args: tuple = (session_id, min(limit, 500))
        else:
            sql = "SELECT * FROM conversation_audit ORDER BY id DESC LIMIT ?"
            args = (min(limit, 500),)
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        out = []
        for r in reversed(rows):
            entry = dict(r)
            entry["tool_calls"] = _json.loads(entry.pop("tool_calls_json", "[]"))
            out.append(entry)
        return out

    def cleanup_old_audits(self, retention_days: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with self._write_lock, self._write_conn as conn:
            cur = conn.execute("DELETE FROM conversation_audit WHERE ts < ?", (cutoff,))
            return cur.rowcount
