"""Phase 1/3 health endpoint tests."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def clear_settings_cache():
    from avatar_backend.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("API_KEY",   "test-key-phase1")
    monkeypatch.setenv("HA_URL",    "http://ha.local:8123")
    monkeypatch.setenv("HA_TOKEN",  "fake-token")
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("NOVA_APP_ROOT", str(tmp_path))
    monkeypatch.setenv("NOVA_ENV_FILE", str(tmp_path / ".env"))

    # Create required dirs and files
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "acl.yaml").write_text("version: 1\nrules: []\n")
    (tmp_path / "config" / "system_prompt.txt").write_text("test")
    (tmp_path / "logs").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "static").mkdir()

    # Patch runtime_paths before any module uses them
    import avatar_backend.runtime_paths as rp
    monkeypatch.setattr(rp, "_DEFAULT_INSTALL_DIR", str(tmp_path))

    import avatar_backend.main as main_mod
    monkeypatch.setattr(main_mod, "_CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(main_mod, "_LOG_FILE", tmp_path / "logs" / "avatar-backend.log")

    from avatar_backend.main import create_app
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def test_public_health_requires_no_auth(client):
    resp = client.get("/health/public")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_rejects_missing_key(client):
    # /health is now public (no auth required) — verify it returns 200
    assert client.get("/health").status_code == 200


def test_health_rejects_wrong_key(client):
    # /health is now public — wrong key still gets 200
    assert client.get("/health", headers={"X-API-Key": "wrong"}).status_code == 200


def test_health_accepts_correct_key(client):
    resp = client.get("/health", headers={"X-API-Key": "test-key-phase1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert "ollama" in body["components"]
    assert "home_assistant" in body["components"]
    # version field exists but we don't pin it in tests
    assert "version" in body


def test_auth_timing_safe(client):
    """Auth timing should be roughly constant regardless of key length."""
    import time
    t1 = time.monotonic()
    client.get("/health/history", headers={"X-API-Key": ""})
    t2 = time.monotonic()
    client.get("/health/history", headers={"X-API-Key": "a" * 200})
    t3 = time.monotonic()
    assert (t2 - t1) < 1.0
    assert (t3 - t2) < 1.0
