"""Gemini provider hard-deadline regression tests.

Production failure: asyncio.wait_for() awaits the cancelled task's completion,
so a SDK retry loop that defers CancelledError (tenacity/aiohttp DNS resolution)
hung "successful" fallbacks for 12-139 minutes. The provider now uses
asyncio.wait + abandon, and bounds the SDK's own retries via HttpOptions.
"""
import asyncio
import time

import pytest

from core.ai_gateway.providers.gemini import GeminiProvider


def _provider(timeout=0.3):
    return GeminiProvider(api_key="test", request_timeout=timeout)


def test_sdk_retry_and_timeout_bounded():
    p = _provider(30)
    opts = p.client._api_client._http_options
    assert opts.timeout == 30_000  # milliseconds
    assert opts.retry_options.attempts == 2
    assert opts.retry_options.max_delay <= 2.0


def test_deadline_holds_when_cancellation_is_deferred(monkeypatch):
    p = _provider(0.3)

    async def deferring(**kwargs):
        # Simulates the SDK retry loop: cancellation arrives but is deferred
        # (work continues briefly before the coroutine finishes).
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            await asyncio.sleep(0.2)
            return "late"
        return "ok"

    monkeypatch.setattr(p.client.aio.models, "generate_content", deferring)
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        asyncio.run(p.generate("prompt"))
    elapsed = time.monotonic() - start
    assert elapsed < 1.5, f"deadline not enforced: {elapsed:.2f}s"


def test_health_check_reports_unhealthy_on_deferred_cancellation(monkeypatch):
    p = _provider(0.3)

    async def deferring(**kwargs):
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            await asyncio.sleep(0.2)
            return "late"
        return "ok"

    monkeypatch.setattr(p.client.aio.models, "generate_content", deferring)
    result = asyncio.run(p.health_check())
    assert result["healthy"] is False
    assert "timed out" in result["error"]


def test_success_path_unchanged(monkeypatch):
    p = _provider(5)

    class Resp:
        text = "hello"
        usage_metadata = None

    async def ok(**kwargs):
        return Resp()

    monkeypatch.setattr(p.client.aio.models, "generate_content", ok)
    out = asyncio.run(p.generate("prompt"))
    assert out["text"] == "hello"


def test_sdk_error_propagates(monkeypatch):
    p = _provider(5)

    async def boom(**kwargs):
        raise RuntimeError("429 resource exhausted")

    monkeypatch.setattr(p.client.aio.models, "generate_content", boom)
    with pytest.raises(RuntimeError):
        asyncio.run(p.generate("prompt"))