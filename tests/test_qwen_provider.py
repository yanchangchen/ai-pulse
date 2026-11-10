"""Tests for the QwenCloudProvider adapter (OpenAI-compatible API)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.ai_gateway.providers.qwen import QwenCloudProvider


def _response(payload, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _chat_payload(text="Hello from Qwen.", prompt_tokens=11, completion_tokens=5):
    return {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def test_qwen_provider_has_request_timeout():
    provider = QwenCloudProvider(
        base_url="https://qwen.example/v1",
        api_key="test-key",
        model="qwen3.8-flash",
        request_timeout=45.0,
    )
    assert provider.request_timeout == 45.0


def test_qwen_provider_default_timeout():
    provider = QwenCloudProvider(
        base_url="https://qwen.example/v1",
        api_key="test-key",
        model="qwen3.8-flash",
    )
    assert provider.request_timeout == 180.0


def test_qwen_generate_raises_timeout():
    provider = QwenCloudProvider(
        base_url="https://qwen.example/v1",
        api_key="test-key",
        model="qwen3.8-flash",
        request_timeout=0.01,
    )

    async def slow_post(*args, **kwargs):
        await asyncio.sleep(10)
        return MagicMock()

    provider.client.post = slow_post
    with pytest.raises(TimeoutError):
        asyncio.run(provider.generate("test prompt"))


def test_qwen_generate_uses_chat_completions_and_parses_response():
    provider = QwenCloudProvider(
        base_url="https://qwen.example/v1",
        api_key="test-key",
        model="qwen3.8-flash",
    )
    provider.client.post = AsyncMock(return_value=_response(_chat_payload()))

    result = asyncio.run(provider.generate("hello", temperature=0.2, max_tokens=99))

    assert result["text"] == "Hello from Qwen."
    assert result["usage"] == {
        "input_tokens": 11,
        "output_tokens": 5,
        "total_tokens": 16,
    }
    assert result["model"] == "qwen3.8-flash"

    args, kwargs = provider.client.post.call_args
    assert args[0] == "/chat/completions"
    payload = kwargs["json"]
    assert payload["model"] == "qwen3.8-flash"
    assert payload["messages"] == [{"role": "user", "content": "hello"}]
    assert payload["temperature"] == 0.2
    assert payload["max_tokens"] == 99
    assert payload["stream"] is False


def test_qwen_structured_strips_markdown_fences():
    provider = QwenCloudProvider(
        base_url="https://qwen.example/v1",
        api_key="test-key",
        model="qwen3.8-flash",
    )
    fenced = '```json\n{"category": "AI Security & Trust"}\n```'
    provider.client.post = AsyncMock(return_value=_response(_chat_payload(fenced)))

    result = asyncio.run(
        provider.generate_structured("categorise", {"type": "object"})
    )

    assert json.loads(result["json"]) == {"category": "AI Security & Trust"}


def test_qwen_health_check_accepts_reachable_endpoint():
    provider = QwenCloudProvider(
        base_url="https://qwen.example/v1",
        api_key="test-key",
        model="qwen3.8-flash",
    )
    provider.client.get = AsyncMock(
        return_value=_response({"data": [{"id": "qwen3.8-flash"}]})
    )

    result = asyncio.run(provider.health_check())

    assert result["healthy"] is True
    assert result["model"] == "qwen3.8-flash"


def test_qwen_health_check_reports_missing_model():
    provider = QwenCloudProvider(
        base_url="https://qwen.example/v1",
        api_key="test-key",
        model="qwen3.8-flash",
    )
    provider.client.get = AsyncMock(
        return_value=_response({"data": [{"id": "some-other-model"}]})
    )

    result = asyncio.run(provider.health_check())

    assert result["healthy"] is False