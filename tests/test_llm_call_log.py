"""Tests for the durable llm_calls observability log (Phase 0.2):
Provenance token capture, gateway usage propagation, and the
availability-gated Supabase mirror (core/ai_gateway/call_log.py).

All tests are offline: providers and the Supabase manager are fakes.
"""

import asyncio
from unittest.mock import MagicMock, patch

from core.ai_gateway import call_log
from core.ai_gateway.contracts import AITaskRequest, Provenance, TaskType
from core.ai_gateway.gateway import ModelGateway, ModelHealth


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeProvider:
    """ProviderAdapter stand-in (no network) with controllable usage."""

    def __init__(self, usage=None, text="ok.", error=None):
        self.usage = usage
        self.text = text
        self.error = error

    def get_capabilities(self):
        return {"provider": "ollama", "model": "fake:cloud", "context_window": 131072}

    async def generate(self, prompt, **kwargs):
        if self.error:
            raise self.error
        return {"text": self.text, "usage": self.usage, "model": "fake:cloud"}

    async def generate_structured(self, prompt, schema, **kwargs):
        if self.error:
            raise self.error
        return {"json": "{}", "usage": self.usage, "model": "fake:cloud"}

    async def health_check(self):
        return {"healthy": self.error is None}


def _gateway_with(provider) -> ModelGateway:
    """Gateway with one injected fake provider for the sage_chat task."""
    gw = ModelGateway(routing_config={
        "model_registry": {
            "m1": {
                "provider": "ollama",
                "model": "fake:cloud",
                "tasks": ["sage_chat"],
                "cost_class": "medium",
            },
        },
        "routing_policies": {
            "sage_chat": {"primary": "m1", "fallback": [], "deterministic_fallback": False},
        },
    })
    gw.providers = {"m1": provider}
    gw.health = {"m1": ModelHealth(provider="ollama", model="fake:cloud")}
    return gw


# ---------------------------------------------------------------------------
# Provenance token fields
# ---------------------------------------------------------------------------

class TestProvenanceTokens:
    def test_roundtrip(self):
        prov = Provenance(
            task="sage_chat", method="llm", provider="ollama",
            model="m", tokens_in=11, tokens_out=7,
        )
        d = prov.to_dict()
        assert d["tokens_in"] == 11
        assert d["tokens_out"] == 7
        restored = Provenance.from_dict(d)
        assert restored.tokens_in == 11
        assert restored.tokens_out == 7

    def test_defaults_none(self):
        d = Provenance(task="x", method="llm").to_dict()
        assert d["tokens_in"] is None
        assert d["tokens_out"] is None


# ---------------------------------------------------------------------------
# Gateway usage propagation
# ---------------------------------------------------------------------------

class TestGatewayUsagePropagation:
    def test_usage_flows_to_provenance(self):
        gw = _gateway_with(FakeProvider(usage={"input_tokens": 123, "output_tokens": 45}))
        result = asyncio.run(gw.execute(AITaskRequest(task=TaskType.SAGE_CHAT, input="hello")))
        assert result.is_success()
        assert result.provenance.tokens_in == 123
        assert result.provenance.tokens_out == 45

    def test_missing_usage_leaves_none(self):
        gw = _gateway_with(FakeProvider(usage=None))
        result = asyncio.run(gw.execute(AITaskRequest(task=TaskType.SAGE_CHAT, input="hello")))
        assert result.is_success()
        assert result.provenance.tokens_in is None
        assert result.provenance.tokens_out is None


# ---------------------------------------------------------------------------
# record_call: row shape + availability gating
# ---------------------------------------------------------------------------

class TestRecordCall:
    def _prov(self, **kw):
        return Provenance(
            task="sage_chat", method="llm", provider="ollama",
            model="nemotron-3-ultra:cloud", latency_ms=1500,
            correlation_id="abc12345", **kw,
        )

    def _patch_manager(self, manager):
        return patch("core.supabase_client.get_supabase_manager", return_value=manager)

    def test_row_shape_inserted(self):
        manager = MagicMock()
        manager.is_available.return_value = True
        with self._patch_manager(manager):
            thread = call_log.record_call(
                self._prov(tokens_in=10, tokens_out=20), success=True, cost_class="medium"
            )
        assert thread is not None
        thread.join(timeout=5)

        row = manager.insert_llm_call.call_args.args[0]
        assert row["task_type"] == "sage_chat"
        assert row["provider"] == "ollama"
        assert row["model"] == "nemotron-3-ultra:cloud"
        assert row["tokens_in"] == 10
        assert row["tokens_out"] == 20
        assert row["latency_ms"] == 1500
        assert row["cost_class"] == "medium"
        assert row["correlation_id"] == "abc12345"
        assert row["success"] is True
        assert "error_text" not in row

    def test_failure_row_carries_capped_error_text(self):
        manager = MagicMock()
        manager.is_available.return_value = True
        with self._patch_manager(manager):
            thread = call_log.record_call(
                self._prov(error="x" * 2000), success=False, cost_class=None
            )
        thread.join(timeout=5)

        row = manager.insert_llm_call.call_args.args[0]
        assert row["success"] is False
        assert row["error_text"] == "x" * call_log._MAX_ERROR_CHARS

    def test_unavailable_manager_skips_insert(self):
        manager = MagicMock()
        manager.is_available.return_value = False
        with self._patch_manager(manager):
            thread = call_log.record_call(self._prov(), success=True)
        assert thread is None
        manager.insert_llm_call.assert_not_called()

    def test_insert_failure_is_swallowed(self):
        manager = MagicMock()
        manager.is_available.return_value = True
        manager.insert_llm_call.side_effect = RuntimeError("table missing")
        with self._patch_manager(manager):
            thread = call_log.record_call(self._prov(), success=True)
        thread.join(timeout=5)  # must not raise


# ---------------------------------------------------------------------------
# Gateway hook: logging must never break the task
# ---------------------------------------------------------------------------

class TestGatewayLoggingHook:
    def test_success_call_is_logged_with_cost_class(self):
        gw = _gateway_with(FakeProvider(usage={"input_tokens": 1, "output_tokens": 2}))
        with patch("core.ai_gateway.call_log.record_call") as mock_record:
            result = asyncio.run(gw.execute(AITaskRequest(task=TaskType.SAGE_CHAT, input="hello")))

        assert result.is_success()
        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["success"] is True
        assert mock_record.call_args.kwargs["cost_class"] == "medium"
        assert mock_record.call_args.args[0].tokens_in == 1

    def test_failure_call_is_logged(self):
        gw = _gateway_with(FakeProvider(error=RuntimeError("401 unauthorized")))
        with patch("core.ai_gateway.call_log.record_call") as mock_record:
            result = asyncio.run(gw.execute(AITaskRequest(task=TaskType.SAGE_CHAT, input="hello")))

        assert not result.is_success()
        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["success"] is False

    def test_record_call_exception_never_breaks_execute(self):
        gw = _gateway_with(FakeProvider())
        with patch("core.ai_gateway.call_log.record_call", side_effect=RuntimeError("boom")):
            result = asyncio.run(gw.execute(AITaskRequest(task=TaskType.SAGE_CHAT, input="hello")))
        assert result.is_success()
