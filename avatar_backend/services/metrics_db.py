"""Backward-compatibility shim — real implementation lives in metrics/ package."""
from avatar_backend.services.metrics.db import MetricsDB  # noqa: F401

__all__ = ["MetricsDB"]
