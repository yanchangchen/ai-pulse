"""Tests for gateway health latching, circuit-breaker auto-reset, and
loud fallback logging.

These cover the nemotron regression where the primary model latched
'unavailable' after a transient Ollama Cloud outage and every later
summarisation silently routed to gpt-oss for the process lifetime.
All tests are offline: fake providers are injected directly.
"""

import asyncio
import logging
import time

import pytest

from core.ai_gateway.gateway import ModelGateway, ModelHealth
from core.ai_gateway.contracts import AITaskRequest, TaskType


MINI_ROUTING = {
    "model_registry": {
        "m1": {
            "provider": "ollama",
            "model": "m1:cloud",
            "tasks": ["summarise"],
            "context_window": 131072,
        },
        "m2": {
            "provider": "ollama",
            "model": "m2:cloud",
            "tasks": ["summarise"],
            "context_window": 131072,
        },
    },
    "routing_policies": {
        "summarise": {
            "primary": "m1",
            "fallback": ["m2"],
            "deterministic_fallback": True,
        },
    },
}


class FakeProvider:
    """Minimal ProviderAdapter stand-in (no network)."""

    def __init__(self, model="fake", behaviour="ok", text="Summary text.", error=None):
        self.model = model
        self.behaviour = behaviour  # "ok" | "raise"
        self.text = text
        self.error = error or TimeoutError("simulated timeout after 180s")
        self.calls = 0

    def get_capabilities(self):
        return {
            "provider": "ollama",
            "model": self.model,
            "context_window": 131072,
            "tasks": ["summarise"],
            "supports_structured": False,
        }

    async def generate(self, prompt, **kwargs):
        self.calls += 1
        if self.behaviour == "raise":
            raise self.error
        return {"text": self.text, "usage": None, "model": self.model}

    async def generate_structured(self, prompt, schema, **kwargs):
        self.calls += 1
        if self.behaviour == "raise":
            raise self.error
        return {"json": "{}", "usage": None, "model": self.model}

    async def health_check(self):
        return {"healthy": self.behaviour == "ok", "model": self.model}


def make_gateway(providers):
    """Build a gateway with injected fake providers/health (env-independent)."""
    gw = ModelGateway(routing_config=MINI_ROUTING)
    gw.providers = dict(providers)
    gw.health = {
        name: ModelHealth(provider="ollama", model=p.model)
        for name, p in providers.items()
    }
    gw.health_reset_seconds = 300
    return gw


# ---------------------------------------------------------------------------
# Health latching + circuit-breaker auto-reset
# ---------------------------------------------------------------------------

class TestHealthLatching:
    def test_latches_unavailable_after_five_failures(self):
        gw = make_gateway({"m1": FakeProvider(model="m1:cloud")})
        for _ in range(4):
            gw._record_failure("m1")
        assert gw.health["m1"].status == "degraded"
        assert gw._is_healthy("m1") is True
        gw._record_failure("m1")
        assert gw.health["m1"].status == "unavailable"
        assert gw._is_healthy("m1") is False

    def test_degrades_after_three_failures(self):
        gw = make_gateway({"m1": FakeProvider(model="m1:cloud")})
        for _ in range(3):
            gw._record_failure("m1")
        assert gw.health["m1"].status == "degraded"

    def test_unavailable_within_cooldown_stays_skipped(self):
        gw = make_gateway({"m1": FakeProvider(model="m1:cloud")})
        for _ in range(5):
            gw._record_failure("m1")
        # last failure just happened — cooldown has not elapsed
        assert gw._is_healthy("m1") is False
        assert gw.health["m1"].status == "unavailable"

    def test_auto_reset_after_cooldown(self):
        gw = make_gateway({"m1": FakeProvider(model="m1:cloud")})
        for _ in range(5):
            gw._record_failure("m1")
        # Backdate the last failure past the cooldown window
        gw.health["m1"].last_failure = time.time() - 301
        assert gw._is_healthy("m1") is True
        assert gw.health["m1"].status == "degraded"
        assert gw.health["m1"].consecutive_failures == 3

    def test_two_more_failures_relatch_after_reset(self):
        gw = make_gateway({"m1": FakeProvider(model="m1:cloud")})
        for _ in range(5):
            gw._record_failure("m1")
        gw.health["m1"].last_failure = time.time() - 301
        assert gw._is_healthy("m1") is True  # half-open at 3 strikes
        gw._record_failure("m1")
        assert gw.health["m1"].status == "degraded"  # 4 strikes
        gw._record_failure("m1")
        assert gw.health["m1"].status == "unavailable"  # 5 strikes again

    def test_success_fully_restores_after_reset(self):
        gw = make_gateway({"m1": FakeProvider(model="m1:cloud")})
        for _ in range(5):
            gw._record_failure("m1")
        gw.health["m1"].last_failure = time.time() - 301
        assert gw._is_healthy("m1") is True
        gw._record_success("m1")
        assert gw.health["m1"].status == "healthy"
        assert gw.health["m1"].consecutive_failures == 0

    def test_health_check_all_clears_latched_state(self):
        gw = make_gateway({"m1": FakeProvider(model="m1:cloud", behaviour="ok")})
        gw.health["m1"].status = "unavailable"
        gw.health["m1"].consecutive_failures = 5
        gw.health["m1"].last_failure = time.time()

        results = asyncio.run(gw.health_check_all())

        assert results["m1"]["healthy"] is True
        assert gw.health["m1"].status == "healthy"
        assert gw.health["m1"].consecutive_failures == 0

    def test_health_check_all_keeps_latch_on_unhealthy_probe(self):
        gw = make_gateway({"m1": FakeProvider(model="m1:cloud", behaviour="raise")})
        gw.health["m1"].status = "unavailable"
        gw.health["m1"].consecutive_failures = 5

        asyncio.run(gw.health_check_all())

        assert gw.health["m1"].status == "unavailable"
        assert gw.health["m1"].consecutive_failures == 5


# ---------------------------------------------------------------------------
# Fallback behaviour + loud logging
# ---------------------------------------------------------------------------

class TestFallbackLogging:
    def test_execute_falls_back_and_logs_warning(self, caplog):
        gw = make_gateway({
            "m1": FakeProvider(model="m1:cloud", behaviour="raise"),
            "m2": FakeProvider(model="m2:cloud", behaviour="ok", text="Recovered."),
        })
        request = AITaskRequest(task=TaskType.SUMMARISE, input="Some AI news content.")

        with caplog.at_level(logging.WARNING):
            result = asyncio.run(gw.execute(request))

        assert result.is_success()
        assert result.result == "Recovered."
        assert result.provenance.fallback_used is True
        assert result.provenance.fallback_from == "m1"
        assert result.provenance.model == "m2:cloud"
        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("m1" in m and "failed" in m for m in warnings)

    def test_execute_skips_unhealthy_primary_with_warning(self, caplog):
        gw = make_gateway({
            "m1": FakeProvider(model="m1:cloud", behaviour="raise"),
            "m2": FakeProvider(model="m2:cloud", behaviour="ok", text="From m2."),
        })
        # Latch m1 off with a recent failure (inside cooldown)
        for _ in range(5):
            gw._record_failure("m1")
        request = AITaskRequest(task=TaskType.SUMMARISE, input="Some AI news content.")

        with caplog.at_level(logging.WARNING):
            result = asyncio.run(gw.execute(request))

        assert result.is_success()
        assert result.provenance.model == "m2:cloud"
        # m1 must never have been called — it was skipped, not retried
        assert gw.providers["m1"].calls == 0
        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("Skipping 'm1'" in m for m in warnings)

    def test_all_llms_fail_uses_deterministic_fallback(self, caplog):
        gw = make_gateway({
            "m1": FakeProvider(model="m1:cloud", behaviour="raise"),
            "m2": FakeProvider(model="m2:cloud", behaviour="raise"),
        })
        request = AITaskRequest(
            task=TaskType.SUMMARISE,
            input=(
                "OpenAI released a new model. Enterprise adoption is accelerating. "
                "GPU supply remains constrained across the industry."
            ),
        )

        with caplog.at_level(logging.WARNING):
            result = asyncio.run(gw.execute(request))

        assert result.is_success()
        assert result.provenance.method == "deterministic"
        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("deterministic fallback" in m for m in warnings)
