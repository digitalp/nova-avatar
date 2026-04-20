"""Tests for LLMService — provider selection, fallback, error handling, vision."""
from __future__ import annotations
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from avatar_backend.services.llm_service import LLMService, _resize_for_ollama


# ── Helpers ──────────────────────────────────────────────────────────────────

def _settings(**overrides):
    defaults = dict(
        llm_provider="ollama",
        ollama_url="http://localhost:11434",
        ollama_model="llama3.1:8b",
        ollama_vision_model="llama3.2-vision:11b",
        ollama_vision_url="",
        ollama_local_text_model="",
        proactive_ollama_model="",
        sensor_watch_ollama_model="",
        cloud_model="gpt-4o-mini",
        openai_api_key="",
        google_api_key="",
        anthropic_api_key="",
        motion_vision_provider="",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _ollama_chat_response(content="Hello", tool_calls=None):
    return {
        "message": {"content": content, "tool_calls": tool_calls or []},
        "prompt_eval_count": 10,
        "eval_count": 5,
    }


def _ollama_tags_response(models):
    return {"models": [{"name": m} for m in models]}


def _mock_httpx_post(json_body):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


# ── Provider selection ───────────────────────────────────────────────────────

@patch("avatar_backend.services.llm_service.get_settings")
@patch("avatar_backend.services.llm_service.httpx.Client")
def test_ollama_provider_has_no_fallback(mock_client, mock_settings):
    mock_settings.return_value = _settings()
    mock_client.return_value.__enter__ = lambda s: s
    mock_client.return_value.__exit__ = lambda s, *a: False
    mock_client.return_value.get = MagicMock(return_value=MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(return_value=_ollama_tags_response(["llama3.1:8b"]))
    ))
    svc = LLMService()
    assert svc.provider_name == "ollama"
    assert svc._fallback is None


@patch("avatar_backend.services.llm_service.get_settings")
@patch("avatar_backend.services.llm_service.httpx.Client")
def test_openai_provider_has_ollama_fallback(mock_client, mock_settings):
    mock_settings.return_value = _settings(
        llm_provider="openai", openai_api_key="sk-test"
    )
    mock_client.return_value.__enter__ = lambda s: s
    mock_client.return_value.__exit__ = lambda s, *a: False
    mock_client.return_value.get = MagicMock(return_value=MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(return_value=_ollama_tags_response(["gemma2:9b"]))
    ))
    svc = LLMService()
    assert svc.provider_name == "openai"
    assert svc._fallback is not None


@patch("avatar_backend.services.llm_service.get_settings")
@patch("avatar_backend.services.llm_service.httpx.Client")
def test_google_provider_selected(mock_client, mock_settings):
    mock_settings.return_value = _settings(
        llm_provider="google", google_api_key="gk-test", cloud_model="gemini-2.0-flash"
    )
    mock_client.return_value.__enter__ = lambda s: s
    mock_client.return_value.__exit__ = lambda s, *a: False
    mock_client.return_value.get = MagicMock(return_value=MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(return_value=_ollama_tags_response(["gemma2:9b"]))
    ))
    svc = LLMService()
    assert svc.provider_name == "google"
    assert svc._fallback is not None


# ── Chat fallback ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("avatar_backend.services.llm_service.get_settings")
@patch("avatar_backend.services.llm_service.httpx.Client")
async def test_chat_falls_back_on_connect_error(mock_client, mock_settings):
    mock_settings.return_value = _settings(
        llm_provider="openai", openai_api_key="sk-test"
    )
    mock_client.return_value.__enter__ = lambda s: s
    mock_client.return_value.__exit__ = lambda s, *a: False
    mock_client.return_value.get = MagicMock(return_value=MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(return_value=_ollama_tags_response(["gemma2:9b"]))
    ))
    svc = LLMService()
    # Primary raises ConnectError, fallback succeeds
    svc._backend.chat = AsyncMock(side_effect=httpx.ConnectError("refused"))
    svc._fallback.chat = AsyncMock(return_value=("fallback reply", []))

    text, tools = await svc.chat([{"role": "user", "content": "hi"}])
    assert text == "fallback reply"
    svc._fallback.chat.assert_awaited_once()


@pytest.mark.asyncio
@patch("avatar_backend.services.llm_service.get_settings")
@patch("avatar_backend.services.llm_service.httpx.Client")
async def test_chat_falls_back_on_timeout(mock_client, mock_settings):
    mock_settings.return_value = _settings(
        llm_provider="google", google_api_key="gk-test"
    )
    mock_client.return_value.__enter__ = lambda s: s
    mock_client.return_value.__exit__ = lambda s, *a: False
    mock_client.return_value.get = MagicMock(return_value=MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(return_value=_ollama_tags_response(["gemma2:9b"]))
    ))
    svc = LLMService()
    svc._backend.chat = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
    svc._fallback.chat = AsyncMock(return_value=("fallback", []))

    text, _ = await svc.chat([{"role": "user", "content": "hi"}])
    assert text == "fallback"


@pytest.mark.asyncio
@patch("avatar_backend.services.llm_service.get_settings")
@patch("avatar_backend.services.llm_service.httpx.Client")
async def test_chat_raises_when_ollama_only_and_fails(mock_client, mock_settings):
    mock_settings.return_value = _settings()
    mock_client.return_value.__enter__ = lambda s: s
    mock_client.return_value.__exit__ = lambda s, *a: False
    mock_client.return_value.get = MagicMock(return_value=MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(return_value=_ollama_tags_response(["llama3.1:8b"]))
    ))
    svc = LLMService()
    svc._backend.chat = AsyncMock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(RuntimeError, match="LLM unavailable"):
        await svc.chat([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
@patch("avatar_backend.services.llm_service.get_settings")
@patch("avatar_backend.services.llm_service.httpx.Client")
async def test_chat_raises_when_both_primary_and_fallback_fail(mock_client, mock_settings):
    mock_settings.return_value = _settings(
        llm_provider="openai", openai_api_key="sk-test"
    )
    mock_client.return_value.__enter__ = lambda s: s
    mock_client.return_value.__exit__ = lambda s, *a: False
    mock_client.return_value.get = MagicMock(return_value=MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(return_value=_ollama_tags_response(["gemma2:9b"]))
    ))
    svc = LLMService()
    svc._backend.chat = AsyncMock(side_effect=httpx.ConnectError("refused"))
    svc._fallback.chat = AsyncMock(side_effect=httpx.ConnectError("also refused"))

    with pytest.raises(RuntimeError, match="fallback also failed"):
        await svc.chat([{"role": "user", "content": "hi"}])


# ── generate_text fallback ───────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("avatar_backend.services.llm_service.get_settings")
@patch("avatar_backend.services.llm_service.httpx.Client")
async def test_generate_text_falls_back(mock_client, mock_settings):
    mock_settings.return_value = _settings(
        llm_provider="openai", openai_api_key="sk-test"
    )
    mock_client.return_value.__enter__ = lambda s: s
    mock_client.return_value.__exit__ = lambda s, *a: False
    mock_client.return_value.get = MagicMock(return_value=MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(return_value=_ollama_tags_response(["gemma2:9b"]))
    ))
    svc = LLMService()
    svc._backend.generate_text = AsyncMock(
        side_effect=httpx.HTTPStatusError("429", request=MagicMock(), response=MagicMock())
    )
    svc._fallback.generate_text = AsyncMock(return_value="fallback text")

    result = await svc.generate_text("prompt")
    assert result == "fallback text"


# ── Local resilient retry + cloud fallback ───────────────────────────────────

@pytest.mark.asyncio
@patch("avatar_backend.services.llm_service.get_settings")
@patch("avatar_backend.services.llm_service.httpx.Client")
async def test_local_resilient_retries_then_falls_back_to_cloud(mock_client, mock_settings):
    mock_settings.return_value = _settings(
        llm_provider="google", google_api_key="gk-test"
    )
    mock_client.return_value.__enter__ = lambda s: s
    mock_client.return_value.__exit__ = lambda s, *a: False
    mock_client.return_value.get = MagicMock(return_value=MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(return_value=_ollama_tags_response(["gemma2:9b"]))
    ))
    svc = LLMService()
    # Local backend fails twice
    svc._local_text_backend.generate_text = AsyncMock(
        side_effect=httpx.ConnectError("refused")
    )
    # Cloud fallback via generate_text → primary backend
    svc._backend.generate_text = AsyncMock(return_value="cloud result")

    result = await svc.generate_text_local_resilient(
        "prompt", retry_delay_s=0.01
    )
    assert result == "cloud result"
    assert svc._local_text_backend.generate_text.await_count == 2


@pytest.mark.asyncio
@patch("avatar_backend.services.llm_service.get_settings")
@patch("avatar_backend.services.llm_service.httpx.Client")
async def test_local_resilient_raises_when_ollama_only(mock_client, mock_settings):
    mock_settings.return_value = _settings()
    mock_client.return_value.__enter__ = lambda s: s
    mock_client.return_value.__exit__ = lambda s, *a: False
    mock_client.return_value.get = MagicMock(return_value=MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(return_value=_ollama_tags_response(["llama3.1:8b"]))
    ))
    svc = LLMService()
    svc._local_text_backend.generate_text = AsyncMock(
        side_effect=httpx.ConnectError("refused")
    )

    with pytest.raises(RuntimeError, match="Local LLM unavailable"):
        await svc.generate_text_local_resilient("prompt", retry_delay_s=0.01)


# ── describe_image fallback ──────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("avatar_backend.services.llm_service._vision_ollama_url", return_value="http://localhost:11434")
@patch("avatar_backend.services.llm_service._ollama_describe_image", new_callable=AsyncMock)
@patch("avatar_backend.services.llm_service._gemini_describe_image", new_callable=AsyncMock)
@patch("avatar_backend.services.llm_service.get_settings")
@patch("avatar_backend.services.llm_service.httpx.Client")
async def test_describe_image_gemini_falls_back_to_ollama(
    mock_client, mock_settings, mock_gemini, mock_ollama, _mock_url
):
    s = _settings(
        llm_provider="google", google_api_key="gk-test", cloud_model="gemini-2.0-flash"
    )
    mock_settings.return_value = s
    mock_client.return_value.__enter__ = lambda self: self
    mock_client.return_value.__exit__ = lambda self, *a: False
    mock_client.return_value.get = MagicMock(return_value=MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(return_value=_ollama_tags_response(["gemma2:9b"]))
    ))
    svc = LLMService()
    mock_gemini.side_effect = Exception("Gemini 429")
    mock_ollama.return_value = "A person at the door"

    result = await svc.describe_image(b"fake-image")
    assert result == "A person at the door"
    mock_ollama.assert_awaited_once()


@pytest.mark.asyncio
@patch("avatar_backend.services.llm_service._ollama_describe_image", new_callable=AsyncMock)
@patch("avatar_backend.services.llm_service.get_settings")
@patch("avatar_backend.services.llm_service.httpx.Client")
async def test_describe_image_ollama_direct(mock_client, mock_settings, mock_ollama):
    mock_settings.return_value = _settings()
    mock_client.return_value.__enter__ = lambda s: s
    mock_client.return_value.__exit__ = lambda s, *a: False
    mock_client.return_value.get = MagicMock(return_value=MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(return_value=_ollama_tags_response(["llama3.1:8b"]))
    ))
    svc = LLMService()
    mock_ollama.return_value = "A car in the driveway"

    result = await svc.describe_image(b"fake-image")
    assert result == "A car in the driveway"


@pytest.mark.asyncio
@patch("avatar_backend.services.llm_service._ollama_describe_image", new_callable=AsyncMock)
@patch("avatar_backend.services.llm_service.get_settings")
@patch("avatar_backend.services.llm_service.httpx.Client")
async def test_describe_image_returns_graceful_message_on_total_failure(
    mock_client, mock_settings, mock_ollama
):
    mock_settings.return_value = _settings()
    mock_client.return_value.__enter__ = lambda s: s
    mock_client.return_value.__exit__ = lambda s, *a: False
    mock_client.return_value.get = MagicMock(return_value=MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(return_value=_ollama_tags_response(["llama3.1:8b"]))
    ))
    svc = LLMService()
    mock_ollama.side_effect = Exception("GPU OOM")

    result = await svc.describe_image(b"fake-image")
    assert "couldn't analyze" in result.lower()


# ── chat_operational fallback ────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("avatar_backend.services.llm_service.get_settings")
@patch("avatar_backend.services.llm_service.httpx.Client")
async def test_chat_operational_falls_back_to_default(mock_client, mock_settings):
    mock_settings.return_value = _settings(
        llm_provider="ollama", google_api_key="gk-test"
    )
    mock_client.return_value.__enter__ = lambda s: s
    mock_client.return_value.__exit__ = lambda s, *a: False
    mock_client.return_value.get = MagicMock(return_value=MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(return_value=_ollama_tags_response(["llama3.1:8b"]))
    ))
    svc = LLMService()
    # Operational backend (Gemini) fails
    if svc._operational_backend is not None:
        svc._operational_backend.chat = AsyncMock(
            side_effect=httpx.ConnectError("refused")
        )
    # Default backend succeeds
    svc._backend.chat = AsyncMock(return_value=("default reply", []))

    text, _ = await svc.chat_operational(
        [{"role": "user", "content": "hi"}]
    )
    assert text == "default reply"


# ── _resize_for_ollama ──────────────────────────────────────────────────────

def test_resize_returns_original_if_small():
    # 1x1 JPEG — smaller than max_width
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (640, 480)).save(buf, format="JPEG")
    original = buf.getvalue()
    result = _resize_for_ollama(original, max_width=1280)
    assert result == original


def test_resize_downscales_large_image():
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (2560, 1440)).save(buf, format="JPEG")
    original = buf.getvalue()
    result = _resize_for_ollama(original, max_width=1280)
    img = Image.open(io.BytesIO(result))
    assert img.width == 1280
    assert img.height == 720


def test_resize_returns_original_on_bad_data():
    result = _resize_for_ollama(b"not-an-image")
    assert result == b"not-an-image"


# ── Properties ───────────────────────────────────────────────────────────────

@patch("avatar_backend.services.llm_service.get_settings")
@patch("avatar_backend.services.llm_service.httpx.Client")
def test_model_name_property(mock_client, mock_settings):
    mock_settings.return_value = _settings()
    mock_client.return_value.__enter__ = lambda s: s
    mock_client.return_value.__exit__ = lambda s, *a: False
    mock_client.return_value.get = MagicMock(return_value=MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(return_value=_ollama_tags_response(["llama3.1:8b"]))
    ))
    svc = LLMService()
    assert svc.model_name == "llama3.1:8b"
    assert svc.provider_name == "ollama"
