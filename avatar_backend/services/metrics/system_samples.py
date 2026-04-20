"""System metrics samples mixin for MetricsDB."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta


class SystemSamplesMixin:

    def insert_sample(self, s: dict) -> None:
        sql = """INSERT INTO system_samples
                 (ts, cpu_pct, ram_used, ram_total, disk_used, disk_total,
                  gpu_util, gpu_mem_used, gpu_mem_total, ollama_gpu_pct)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        ts = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self._write_conn as conn:
            conn.execute(sql, (
                ts,
                s.get("cpu_pct"),
                s.get("ram_used"),
                s.get("ram_total"),
                s.get("disk_used"),
                s.get("disk_total"),
                s.get("gpu_util"),
                s.get("gpu_mem_used"),
                s.get("gpu_mem_total"),
                s.get("ollama_gpu_pct"),
            ))

    def recent_samples(self, minutes: int = 60) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        sql = "SELECT * FROM system_samples WHERE ts >= ? ORDER BY ts"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, (since,)).fetchall()]

    def latest_sample(self) -> dict | None:
        sql = "SELECT * FROM system_samples ORDER BY id DESC LIMIT 1"
        with self._conn() as conn:
            row = conn.execute(sql).fetchone()
            return dict(row) if row else None

    def hourly_averages(self, hours: int = 24) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        sql = """SELECT strftime('%Y-%m-%dT%H:00:00Z', ts) hour,
                        AVG(cpu_pct) cpu_pct,
                        AVG(ram_used) ram_used, MAX(ram_total) ram_total,
                        AVG(gpu_util) gpu_util,
                        AVG(gpu_mem_used) gpu_mem_used, MAX(gpu_mem_total) gpu_mem_total
                 FROM system_samples WHERE ts >= ?
                 GROUP BY hour ORDER BY hour"""
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, (since,)).fetchall()]
