"""LLM cost tracking mixin for MetricsDB."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta


class LLMCostsMixin:

    def insert_invocation(self, entry: dict) -> None:
        sql = """INSERT INTO llm_invocations
                 (ts, provider, model, purpose, input_tokens, output_tokens, cost_usd, elapsed_ms)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""
        ts = datetime.now(timezone.utc).isoformat()
        with self._write_lock, self._write_conn as conn:
            conn.execute(sql, (
                ts,
                entry.get("provider", ""),
                entry.get("model", ""),
                entry.get("purpose", "chat"),
                entry.get("input_tokens", 0),
                entry.get("output_tokens", 0),
                entry.get("cost_usd", 0.0),
                entry.get("elapsed_ms", 0),
            ))

    def cost_summary(self, period: str = "month") -> dict:
        """Return aggregated cost + token totals for the given period."""
        now = datetime.now(timezone.utc)
        if period == "day":
            since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "week":
            since = now - timedelta(days=now.weekday())
            since = since.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "month":
            since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elif period == "year":
            since = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            since = now - timedelta(hours=24)

        sql = """SELECT COUNT(*) calls,
                        COALESCE(SUM(input_tokens),0)  input_tokens,
                        COALESCE(SUM(output_tokens),0) output_tokens,
                        COALESCE(SUM(cost_usd),0)      cost_usd
                 FROM llm_invocations WHERE ts >= ?"""
        with self._conn() as conn:
            row = conn.execute(sql, (since.isoformat(),)).fetchone()
            return dict(row) if row else {}

    def cost_by_day(self, days: int = 30) -> list[dict]:
        """Return daily cost totals for the last N days."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        sql = """SELECT strftime('%Y-%m-%d', ts) day,
                        COUNT(*) calls,
                        SUM(input_tokens)  input_tokens,
                        SUM(output_tokens) output_tokens,
                        SUM(cost_usd)      cost_usd
                 FROM llm_invocations WHERE ts >= ?
                 GROUP BY day ORDER BY day"""
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, (since,)).fetchall()]

    def cost_by_model(self, period: str = "month") -> list[dict]:
        """Return cost breakdown by model for the given period."""
        summary = self.cost_summary.__wrapped__ if hasattr(self.cost_summary, '__wrapped__') else None
        now = datetime.now(timezone.utc)
        if period == "month":
            since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elif period == "year":
            since = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            since = now - timedelta(hours=24)
        sql = """SELECT provider, model,
                        COUNT(*) calls,
                        SUM(input_tokens)  input_tokens,
                        SUM(output_tokens) output_tokens,
                        SUM(cost_usd)      cost_usd
                 FROM llm_invocations WHERE ts >= ?
                 GROUP BY provider, model ORDER BY cost_usd DESC"""
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, (since.isoformat(),)).fetchall()]

    def monthly_totals(self, months: int = 12) -> list[dict]:
        """Return monthly cost totals for the last N months."""
        since = (datetime.now(timezone.utc) - timedelta(days=months * 31)).isoformat()
        sql = """SELECT strftime('%Y-%m', ts) month,
                        COUNT(*) calls,
                        SUM(input_tokens)  input_tokens,
                        SUM(output_tokens) output_tokens,
                        SUM(cost_usd)      cost_usd
                 FROM llm_invocations WHERE ts >= ?
                 GROUP BY month ORDER BY month"""
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, (since,)).fetchall()]

    def recent_invocations(self, n: int = 200) -> list[dict]:
        """Return the most recent persisted LLM invocation rows."""
        sql = """SELECT ts, provider, model, purpose, input_tokens, output_tokens,
                        cost_usd, elapsed_ms
                 FROM llm_invocations
                 ORDER BY id DESC
                 LIMIT ?"""
        with self._conn() as conn:
            rows = conn.execute(sql, (n,)).fetchall()
        out: list[dict] = []
        for row in reversed(rows):
            entry = dict(row)
            ts = entry.get("ts", "")
            if isinstance(ts, str) and len(ts) >= 19:
                entry["ts"] = ts[11:19]
            out.append(entry)
        return out
