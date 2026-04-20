"""Shared test fixtures for Nova V1."""
from __future__ import annotations
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Minimal env vars required by get_settings() ──────────────────────────────
# Set before any avatar_backend imports to prevent ValidationError on API_KEY.
import tempfile as _tempfile
_test_root = _tempfile.mkdtemp(prefix="nova_test_")
os.environ.setdefault("NOVA_APP_ROOT", _test_root)
os.environ.setdefault("NOVA_ENV_FILE", os.path.join(_test_root, ".env"))
os.environ.setdefault("API_KEY", "test-key-default")
os.environ.setdefault("HA_URL", "http://ha.local:8123")
os.environ.setdefault("HA_TOKEN", "fake-token")
os.makedirs(os.path.join(_test_root, "data"), exist_ok=True)
os.makedirs(os.path.join(_test_root, "logs"), exist_ok=True)
os.makedirs(os.path.join(_test_root, "config"), exist_ok=True)

from avatar_backend.models.acl import ACLConfig, ACLRule, ACLManager
from avatar_backend.services.metrics_db import MetricsDB


@pytest.fixture(autouse=True)
def _clear_ollama_tags_cache():
    """Reset the Ollama tags cache between tests."""
    import avatar_backend.services.llm_service as llm_mod
    llm_mod._ollama_tags_cache = None
    yield
    llm_mod._ollama_tags_cache = None


# ── ACL fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def acl_permissive() -> ACLManager:
    """ACL that allows light and switch domains only."""
    return ACLManager(ACLConfig(version=1, rules=[
        ACLRule(domain="light", entities="*",
                services=["turn_on", "turn_off", "toggle"]),
        ACLRule(domain="switch", entities=["switch.garden_pump"],
                services=["turn_on", "turn_off"]),
    ]))


# ── Database fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def metrics_db(tmp_path) -> MetricsDB:
    """Fresh in-memory MetricsDB for each test."""
    return MetricsDB(path=tmp_path / "test_metrics.db")
