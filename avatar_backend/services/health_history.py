"""
HealthHistoryService — thin wrapper around MetricsDB health-check persistence.
"""
from __future__ import annotations

import structlog

from avatar_backend.services.metrics_db import MetricsDB

logger = structlog.get_logger()


class HealthHistoryService:
    """Records and queries per-component health-check history."""

    def __init__(self, db: MetricsDB) -> None:
        self._db = db

    def record_check(self, component: str, status: str) -> None:
        """Persist a single probe result. Caller should wrap in try/except."""
        self._db.insert_health_check(component, status)

    def get_history(
        self,
        *,
        component: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict]:
        """Return health-check rows, optionally filtered."""
        return self._db.get_health_history(
            component=component, since=since, until=until,
        )
