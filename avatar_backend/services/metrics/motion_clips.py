"""Motion clip archive mixin for MetricsDB."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Any


class MotionClipsMixin:

    @staticmethod
    def _attach_motion_clip_event_fields(entry: dict[str, Any]) -> dict[str, Any]:
        extra = entry.get("extra") or {}
        canonical = extra.get("canonical_event") if isinstance(extra, dict) else None
        if isinstance(canonical, dict):
            entry["canonical_event_id"] = canonical.get("event_id", "")
            entry["canonical_event_type"] = canonical.get("event_type", "")
            entry["canonical_event"] = canonical
        else:
            entry["canonical_event_id"] = ""
            entry["canonical_event_type"] = ""
        return entry

    def insert_motion_clip(self, entry: dict[str, Any]) -> int:
        import json as _json

        sql = """
        INSERT INTO motion_clips
        (ts, camera_entity_id, trigger_entity_id, location, description,
         video_relpath, thumb_relpath, status, duration_s, flagged,
         llm_provider, llm_model, extra_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        ts = entry.get("ts") or datetime.now(timezone.utc).isoformat()
        with self._write_lock, self._write_conn as conn:
            cur = conn.execute(sql, (
                ts,
                entry.get("camera_entity_id", ""),
                entry.get("trigger_entity_id", ""),
                entry.get("location", ""),
                entry.get("description", ""),
                entry.get("video_relpath", ""),
                entry.get("thumb_relpath", ""),
                entry.get("status", "ready"),
                int(entry.get("duration_s", 0) or 0),
                1 if entry.get("flagged") else 0,
                entry.get("llm_provider", ""),
                entry.get("llm_model", ""),
                _json.dumps(entry.get("extra", {}) or {}),
            ))
            return int(cur.lastrowid)

    def recent_motion_clips(
        self,
        *,
        limit: int = 100,
        date: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        camera_entity_id: str | None = None,
        canonical_event_type: str | None = None,
        flagged_only: bool = False,
    ) -> list[dict[str, Any]]:
        import json as _json

        sql = """
        SELECT id, ts, camera_entity_id, trigger_entity_id, location, description,
               video_relpath, thumb_relpath, status, duration_s, flagged,
               llm_provider, llm_model, extra_json
        FROM motion_clips
        WHERE 1=1
        """
        args: list[Any] = []
        if camera_entity_id:
            sql += " AND camera_entity_id = ?"
            args.append(camera_entity_id)
        if date:
            sql += " AND substr(ts, 1, 10) = ?"
            args.append(date)
        if start_time:
            sql += " AND substr(ts, 12, 5) >= ?"
            args.append(start_time[:5])
        if end_time:
            sql += " AND substr(ts, 12, 5) <= ?"
            args.append(end_time[:5])
        if flagged_only:
            sql += " AND flagged = 1"
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(max(1, min(int(limit), 500)))

        with self._conn() as conn:
            rows = conn.execute(sql, tuple(args)).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            entry = dict(row)
            entry["extra"] = _json.loads(entry.pop("extra_json", "{}") or "{}")
            out.append(self._attach_motion_clip_event_fields(entry))
        if canonical_event_type:
            wanted = canonical_event_type.strip()
            out = [entry for entry in out if str(entry.get("canonical_event_type") or "") == wanted]
        return out

    def get_motion_clip(self, clip_id: int) -> dict[str, Any] | None:
        import json as _json

        sql = """
        SELECT id, ts, camera_entity_id, trigger_entity_id, location, description,
               video_relpath, thumb_relpath, status, duration_s, flagged,
               llm_provider, llm_model, extra_json
        FROM motion_clips
        WHERE id = ?
        """
        with self._conn() as conn:
            row = conn.execute(sql, (clip_id,)).fetchone()
        if not row:
            return None
        entry = dict(row)
        entry["extra"] = _json.loads(entry.pop("extra_json", "{}") or "{}")
        return self._attach_motion_clip_event_fields(entry)

    def delete_motion_clip(self, clip_id: int) -> str | None:
        """Delete a single motion clip. Returns video_relpath so the caller can remove the file."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT video_relpath FROM motion_clips WHERE id = ?", (clip_id,)
            ).fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM motion_clips WHERE id = ?", (clip_id,))
        return row["video_relpath"] or None

    def delete_motion_clips_bulk(self, clip_ids: list[int]) -> list[str]:
        """Delete multiple clips by ID. Returns list of video_relpaths for file cleanup."""
        if not clip_ids:
            return []
        placeholders = ",".join("?" * len(clip_ids))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT id, video_relpath FROM motion_clips WHERE id IN ({placeholders})",
                clip_ids,
            ).fetchall()
            conn.execute(
                f"DELETE FROM motion_clips WHERE id IN ({placeholders})", clip_ids
            )
        return [row["video_relpath"] for row in rows if row["video_relpath"]]

    def delete_all_motion_clips(self) -> list[str]:
        """Delete every motion clip row. Returns all video_relpaths for file cleanup."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT video_relpath FROM motion_clips WHERE video_relpath != ''"
            ).fetchall()
            conn.execute("DELETE FROM motion_clips")
        return [row["video_relpath"] for row in rows]

    def toggle_motion_clip_flag(self, clip_id: int) -> bool:
        """Toggle the flagged state of a clip. Returns the new flagged state."""
        with self._write_lock, self._write_conn as conn:
            row = conn.execute("SELECT flagged FROM motion_clips WHERE id = ?", (clip_id,)).fetchone()
            if not row:
                return False
            new_val = 0 if row["flagged"] else 1
            conn.execute("UPDATE motion_clips SET flagged = ? WHERE id = ?", (new_val, clip_id))
            return bool(new_val)

    def motion_clip_stats(self) -> dict[str, Any]:
        """Return aggregate stats for the motion clip archive."""
        with self._conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN flagged = 1 THEN 1 ELSE 0 END) as flagged,
                       MIN(ts) as oldest_ts,
                       MAX(ts) as newest_ts
                FROM motion_clips
            """).fetchone()
            return {
                "total_clips": row["total"] or 0,
                "flagged_clips": row["flagged"] or 0,
                "oldest_ts": row["oldest_ts"] or "",
                "newest_ts": row["newest_ts"] or "",
            }

    def delete_old_motion_clips(self, older_than_days: int) -> list[str]:
        """Delete clips older than N days. Returns video_relpaths for file cleanup."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        with self._write_lock, self._write_conn as conn:
            rows = conn.execute(
                "SELECT video_relpath, thumb_relpath FROM motion_clips WHERE ts < ? AND flagged = 0",
                (cutoff,),
            ).fetchall()
            conn.execute("DELETE FROM motion_clips WHERE ts < ? AND flagged = 0", (cutoff,))
        paths = []
        for row in rows:
            if row["video_relpath"]:
                paths.append(row["video_relpath"])
            if row["thumb_relpath"]:
                paths.append(row["thumb_relpath"])
        return paths
