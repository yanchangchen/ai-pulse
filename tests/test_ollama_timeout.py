"""Additional tests for the OllamaCloudProvider timeout change."""

import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock


def test_ollama_provider_has_request_timeout():
    from core.ai_gateway.providers.ollama import OllamaCloudProvider
    provider = OllamaCloudProvider(
        base_url="https://api.ollama.com",
        api_key="test-key",
        model="nemotron-3-super:cloud",
        request_timeout=45.0,
    )
    assert provider.request_timeout == 45.0


def test_ollama_provider_default_timeout():
    from core.ai_gateway.providers.ollama import OllamaCloudProvider
    provider = OllamaCloudProvider(
        base_url="https://api.ollama.com",
        api_key="test-key",
        model="nemotron-3-super:cloud",
    )
    assert provider.request_timeout == 60.0


def test_ollama_generate_raises_timeout():
    import asyncio
    from core.ai_gateway.providers.ollama import OllamaCloudProvider
    provider = OllamaCloudProvider(
        base_url="https://api.ollama.com",
        api_key="test-key",
        model="nemotron-3-super:cloud",
        request_timeout=0.01,
    )
    # Simulate a slow response
    async def slow_post(*args, **kwargs):
        await asyncio.sleep(10)
        return MagicMock()

    provider.client.post = slow_post
    with pytest.raises(TimeoutError):
        asyncio.run(provider.generate("test prompt"))
