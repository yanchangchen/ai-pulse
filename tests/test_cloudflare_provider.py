"""Phase 3.5 — Cloudflare Workers AI embedding adapter and provider switch.

All tests are offline: the httpx client is replaced with a mock, and gateway
routing tests inject fake providers / patch module constants.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.ai_gateway.contracts import AITaskRequest, TaskType
from core.ai_gateway.gateway import ModelGateway, ModelHealth
from core.ai_gateway.providers.cloudflare import CloudflareProvider


# ---------------------------------------------------------------------------
# Adapter unit tests
# ---------------------------------------------------------------------------

def _make_provider(model="@cf/baai/bge-base-en-v1.5"):
    with patch("core.ai_gateway.providers.cloudflare.httpx.AsyncClient") as cls:
        provider = CloudflareProvider("acct-123", "token-abc", model=model)
        provider.client = cls.return_value
    provider.client.post = AsyncMock()
    provider.client.aclose = AsyncMock()
    return provider


def _response(payload, raise_exc=None):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = payload
    if raise_exc is not None:
        response.raise_for_status.side_effect = raise_exc
    else:
        response.raise_for_status.return_value = None
    return response


def _embedding_payload(count, dims=768, start=0):
    return {
        "object": "list",
        "model": "@cf/baai/bge-base-en-v1.5",
        "data": [
            {"object": "embedding", "index": start + i, "embedding": [float(i)] * dims}
            for i in range(count)
        ],
        "usage": {"prompt_tokens": 12, "total_tokens": 12},
    }


def test_embed_returns_vectors_in_request_order_and_maps_usage():
    provider = _make_provider()
    # Provider-side order reversed: index must restore request order.
    payload = {
        "data": [
            {"index": 1, "embedding": [1.0] * 768},
            {"index": 0, "embedding": [0.0] * 768},
        ],
        "usage": {"prompt_tokens": 9},
    }
    provider.client.post.return_value = _response(payload)

    result = asyncio.run(provider.embed(["first", "second"], dimensions=768))

    assert result["vectors"][0] == [0.0] * 768
    assert result["vectors"][1] == [1.0] * 768
    assert result["usage"] == {"input_tokens": 9, "output_tokens": 0, "total_tokens": 9}
    assert result["model"] == "@cf/baai/bge-base-en-v1.5"
    provider.client.post.assert_awaited_once_with(
        "/embeddings",
        json={"model": "@cf/baai/bge-base-en-v1.5", "input": ["first", "second"]},
    )


def test_embed_estimates_usage_when_provider_omits_it():
    provider = _make_provider()
    payload = {"data": [{"index": 0, "embedding": [0.0] * 768},
                        {"index": 1, "embedding": [0.0] * 768}]}
    provider.client.post.return_value = _response(payload)

    result = asyncio.run(provider.embed(["first", "second"]))

    # 5//3 + 6//3 = 3, matching the repo's 3-chars/token heuristic.
    assert result["usage"]["input_tokens"] == 3
    assert result["usage"]["estimated"] is True


def test_embed_count_mismatch_rejects_whole_batch():
    provider = _make_provider()
    provider.client.post.return_value = _response(_embedding_payload(1))

    with pytest.raises(ValueError, match="count mismatch"):
        asyncio.run(provider.embed(["a", "b"]))


def test_embed_dimension_mismatch_rejects_whole_batch():
    provider = _make_provider()
    provider.client.post.return_value = _response(_embedding_payload(1, dims=3))

    with pytest.raises(ValueError, match="dimension mismatch"):
        asyncio.run(provider.embed(["a"], dimensions=768))


def test_embed_rejects_missing_data_list():
    provider = _make_provider()
    provider.client.post.return_value = _response({"result": "nope"})

    with pytest.raises(ValueError, match="missing the 'data' list"):
        asyncio.run(provider.embed(["a"]))


def test_embed_rejects_empty_vector():
    provider = _make_provider()
    provider.client.post.return_value = _response({"data": [{"index": 0, "embedding": []}]})

    with pytest.raises(ValueError, match="empty vector"):
        asyncio.run(provider.embed(["a"]))


def test_embed_propagates_http_errors():
    provider = _make_provider()
    error = httpx.HTTPStatusError(
        "429 Too Many Requests",
        request=httpx.Request("POST", "https://example.test/embeddings"),
        response=httpx.Response(429),
    )
    provider.client.post.return_value = _response({}, raise_exc=error)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(provider.embed(["a"]))


def test_embed_timeout_raises_timeout_error():
    provider = _make_provider()
    provider.client.post = AsyncMock(side_effect=asyncio.TimeoutError())

    with pytest.raises(TimeoutError, match="timed out"):
        asyncio.run(provider.embed(["a"]))


def test_generation_methods_are_explicitly_unsupported():
    provider = _make_provider()

    with pytest.raises(NotImplementedError):
        asyncio.run(provider.generate("hello"))
    with pytest.raises(NotImplementedError):
        asyncio.run(provider.generate_structured("hello", {}))


def test_health_check_reports_failure_as_data():
    provider = _make_provider()
    provider.client.post.return_value = _response({}, raise_exc=RuntimeError("401"))

    assert asyncio.run(provider.health_check())["healthy"] is False


def test_requires_account_id_and_token():
    with pytest.raises(ValueError):
        CloudflareProvider("", "token")
    with pytest.raises(ValueError):
        CloudflareProvider("acct", "")


# ---------------------------------------------------------------------------
# Gateway routing / switch behaviour
# ---------------------------------------------------------------------------

def test_embedding_route_defaults_to_gemini_with_no_fallback(monkeypatch):
    import core.ai_gateway.gateway as gwmod

    # Pin the setting explicitly: a developer's secrets.toml may already set
    # EMBEDDING_PROVIDER=cloudflare.
    monkeypatch.setattr(gwmod, "EMBEDDING_PROVIDER", "gemini")
    gateway = ModelGateway()
    policy = gateway.routing_config["routing_policies"]["embedding"]

    assert policy["primary"] == "gemini-embedding-001"
    assert policy["fallback"] == []
    assert policy["deterministic_fallback"] is False


def test_embedding_route_follows_provider_setting(monkeypatch):
    import core.ai_gateway.gateway as gwmod

    monkeypatch.setattr(gwmod, "EMBEDDING_PROVIDER", "cloudflare")
    gateway = ModelGateway()

    policy = gateway.routing_config["routing_policies"]["embedding"]
    assert policy["primary"] == "cloudflare-bge-base-en-v1.5"
    # Never fall back across model spaces (different weights = different space).
    assert policy["fallback"] == []


def test_unknown_embedding_provider_falls_back_to_gemini(monkeypatch):
    import core.ai_gateway.gateway as gwmod

    monkeypatch.setattr(gwmod, "EMBEDDING_PROVIDER", "not-a-provider")
    gateway = ModelGateway()

    assert (
        gateway.routing_config["routing_policies"]["embedding"]["primary"]
        == "gemini-embedding-001"
    )


def test_cloudflare_provider_registered_only_with_credentials(monkeypatch):
    import core.ai_gateway.gateway as gwmod

    created = {}

    class FakeCloudflare:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr(gwmod, "CloudflareProvider", FakeCloudflare)
    monkeypatch.setattr(gwmod, "CF_ACCOUNT_ID", "acct-123")
    monkeypatch.setattr(gwmod, "CF_API_TOKEN", "token-abc")

    gateway = ModelGateway()

    assert "cloudflare-bge-base-en-v1.5" in gateway.providers
    assert created["account_id"] == "acct-123"
    assert created["model"] == gwmod.CLOUDFLARE_EMBEDDING_MODEL


def test_cloudflare_credentials_alone_do_not_switch_route(monkeypatch):
    """The switch is EMBEDDING_PROVIDER, not the presence of credentials."""
    import core.ai_gateway.gateway as gwmod

    class FakeCloudflare:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(gwmod, "CloudflareProvider", FakeCloudflare)
    monkeypatch.setattr(gwmod, "CF_ACCOUNT_ID", "acct-123")
    monkeypatch.setattr(gwmod, "CF_API_TOKEN", "token-abc")
    monkeypatch.setattr(gwmod, "EMBEDDING_PROVIDER", "gemini")

    gateway = ModelGateway()

    assert "cloudflare-bge-base-en-v1.5" in gateway.providers
    assert (
        gateway.routing_config["routing_policies"]["embedding"]["primary"]
        == "gemini-embedding-001"
    )


class _EmbedProvider:
    """Embedding-only fake whose per-text window is tiny (512-token-like)."""

    def __init__(self, dims=768):
        self.dims = dims
        self.texts = None

    def get_capabilities(self):
        return {
            "provider": "cloudflare",
            "model": "fake-embed",
            "context_window": 512,
            "tasks": ["embedding"],
            "supports_structured": False,
        }

    async def embed(self, texts, **kwargs):
        self.texts = list(texts)
        return {
            "vectors": [[0.5] * self.dims for _ in texts],
            "usage": None,
            "model": "fake-embed",
        }

    async def generate(self, prompt, **kwargs):
        raise NotImplementedError

    async def generate_structured(self, prompt, schema, **kwargs):
        raise NotImplementedError

    async def health_check(self):
        return {"healthy": True, "model": "fake-embed"}


def test_embedding_batch_bypasses_whole_batch_context_fit():
    """A 50-article batch exceeds 60% of a 512-token per-text window, but every
    individual text fits — the gateway must still dispatch the batch."""
    routing = {
        "model_registry": {},
        "routing_policies": {
            "embedding": {
                "primary": "cf-embed",
                "fallback": [],
                "deterministic_fallback": False,
            }
        },
    }
    gateway = ModelGateway(routing)
    provider = _EmbedProvider()
    gateway.providers = {"cf-embed": provider}
    gateway.health = {"cf-embed": ModelHealth(provider="cloudflare", model="fake-embed")}

    texts = ["title summary " * 100 for _ in range(50)]  # ~1,300 tokens each
    request = AITaskRequest(
        task=TaskType.EMBEDDING, input="", embed_inputs=texts, embedding_dimensions=768
    )
    result = asyncio.run(gateway.execute(request))

    assert result.is_success()
    assert provider.texts == texts
    assert len(result.result) == 50
    assert all(len(vector) == 768 for vector in result.result)