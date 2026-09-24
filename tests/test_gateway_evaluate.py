"""Tests for the EVALUATE task type and the GatewayJudgeLLM adapter.

The evaluation judges were migrated from the Ollama-only legacy LLMClient
to the Model Gateway so an Ollama quota outage no longer disables them
(EVALUATE routes Gemini-first).  These tests pin:

- the EVALUATE routing policy (Gemini-first, no deterministic fallback)
- verbatim prompt passthrough (no task template wrapping)
- no default output schema (judges keep their own tolerant parsers)
- adapter success/failure semantics (LLMClientError on anything that is
  not a real LLM result, so judges exclude the sample instead of
  scoring a fabricated zero)
"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.ai_gateway.contracts import (
    AITaskRequest,
    AITaskResult,
    Provenance,
    TaskType,
)
from core.ai_gateway.gateway import ModelGateway


def _result(value, method="llm", status="success", error=None):
    prov = Provenance(
        task="evaluate",
        method=method,
        provider="google",
        model="gemini-3.5-flash-lite",
        error=error,
    )
    if status == "success":
        return AITaskResult.success(value, prov)
    return AITaskResult.failure(error or "failed", prov)


# ---------------------------------------------------------------------------
# Routing policy
# ---------------------------------------------------------------------------


class TestEvaluateRouting:
    def test_evaluate_policy_is_gemini_first(self):
        gw = ModelGateway()
        policy = gw._get_routing_policy(TaskType.EVALUATE)
        assert policy["primary"] == "gemini-3.5-flash-lite"
        assert "gemini-3.8-flash" in policy["fallback"]
        # Ollama models remain in the chain as fallbacks.
        assert "nemotron-3-ultra" in policy["fallback"]

    def test_evaluate_has_no_deterministic_fallback(self):
        gw = ModelGateway()
        policy = gw._get_routing_policy(TaskType.EVALUATE)
        assert policy["deterministic_fallback"] is False

    def test_evaluate_prompt_is_verbatim(self):
        gw = ModelGateway()
        req = AITaskRequest(task=TaskType.EVALUATE, input="JUDGE PROMPT VERBATIM")
        assert gw._build_prompt(req) == "JUDGE PROMPT VERBATIM"

    def test_evaluate_has_no_default_schema(self):
        gw = ModelGateway()
        assert gw._get_schema(TaskType.EVALUATE) is None

    def test_evaluate_listed_in_gemini_registry_tasks(self):
        gw = ModelGateway()
        registry = gw.routing_config["model_registry"]
        assert "evaluate" in registry["gemini-3.5-flash-lite"]["tasks"]
        assert "evaluate" in registry["gemini-3.8-flash"]["tasks"]


# ---------------------------------------------------------------------------
# Preferred-model pinning (evaluation "stick to one model" mode)
# ---------------------------------------------------------------------------


class _FakeProvider:
    """Minimal ProviderAdapter stand-in for routing tests (no network)."""

    def __init__(self):
        self.calls = 0

    async def generate(self, prompt, **kwargs):
        self.calls += 1
        return {"text": "verdict"}

    def get_capabilities(self):
        return {"context_window": 262144}


class TestPreferredModelPinning:
    def _gateway_with_fakes(self):
        from core.ai_gateway.gateway import ModelHealth

        gw = ModelGateway()
        nemotron, gemini = _FakeProvider(), _FakeProvider()
        gw.providers = {
            "nemotron-3-ultra": nemotron,
            "gemini-3.5-flash-lite": gemini,
        }
        gw.health = {
            "nemotron-3-ultra": ModelHealth(provider="ollama", model="nemotron-3-ultra:cloud"),
            "gemini-3.5-flash-lite": ModelHealth(provider="google", model="gemini-3.5-flash-lite"),
        }
        return gw, nemotron, gemini

    def test_pinned_model_is_used_exclusively(self):
        import asyncio

        gw, nemotron, gemini = self._gateway_with_fakes()
        req = AITaskRequest(
            task=TaskType.EVALUATE,
            input="JUDGE PROMPT",
            allow_deterministic_fallback=False,
            preferred_model="nemotron-3-ultra",
        )
        result = asyncio.run(gw.execute(req))
        assert result.is_success()
        assert nemotron.calls == 1
        assert gemini.calls == 0  # no cross-model fallback when pinned
        assert result.provenance.model == "nemotron-3-ultra:cloud"

    def test_unknown_preferred_model_uses_default_chain(self):
        import asyncio

        gw, nemotron, gemini = self._gateway_with_fakes()
        req = AITaskRequest(
            task=TaskType.EVALUATE,
            input="JUDGE PROMPT",
            allow_deterministic_fallback=False,
            preferred_model="does-not-exist",
        )
        result = asyncio.run(gw.execute(req))
        # Default EVALUATE chain is Gemini-first, so the Gemini fake serves.
        assert result.is_success()
        assert gemini.calls >= 1
        assert result.provenance.provider == "google"

    def test_no_preference_keeps_full_chain(self):
        import asyncio

        gw, nemotron, gemini = self._gateway_with_fakes()
        req = AITaskRequest(
            task=TaskType.EVALUATE,
            input="JUDGE PROMPT",
            allow_deterministic_fallback=False,
        )
        result = asyncio.run(gw.execute(req))
        assert result.is_success()
        assert gemini.calls == 1
        assert nemotron.calls == 0


# ---------------------------------------------------------------------------
# GatewayJudgeLLM adapter
# ---------------------------------------------------------------------------


class TestGatewayJudgeLLM:
    def _adapter(self):
        from core.evaluator import GatewayJudgeLLM
        return GatewayJudgeLLM(label="test")

    def _gw_with(self, result=None, side_effect=None):
        gw = MagicMock()
        if side_effect is not None:
            gw.execute = AsyncMock(side_effect=side_effect)
        else:
            gw.execute = AsyncMock(return_value=result)
        return gw

    def test_success_returns_text(self):
        gw = self._gw_with(result=_result("verdict text"))
        with patch("core.ai_gateway.gateway.get_gateway", return_value=gw):
            out = self._adapter().generate("prompt")
        assert out == "verdict text"

    def test_request_uses_evaluate_task_without_deterministic_fallback(self):
        gw = self._gw_with(result=_result("ok"))
        with patch("core.ai_gateway.gateway.get_gateway", return_value=gw):
            self._adapter().generate("prompt", temperature=0.2, max_tokens=99)
        req = gw.execute.call_args.args[0]
        assert req.task == TaskType.EVALUATE
        assert req.allow_deterministic_fallback is False
        assert req.temperature == 0.2
        assert req.max_tokens == 99
        assert req.input == "prompt"
        assert req.metadata.get("caller") == "evaluator:test"

    def test_model_key_pins_request_to_one_model(self):
        gw = self._gw_with(result=_result("ok"))
        with patch("core.ai_gateway.gateway.get_gateway", return_value=gw):
            from core.evaluator import GatewayJudgeLLM
            GatewayJudgeLLM(label="test", model_key="gpt-oss-120b").generate("prompt")
        req = gw.execute.call_args.args[0]
        assert req.preferred_model == "gpt-oss-120b"

    def test_no_model_key_leaves_request_unpinned(self):
        gw = self._gw_with(result=_result("ok"))
        with patch("core.ai_gateway.gateway.get_gateway", return_value=gw):
            self._adapter().generate("prompt")
        req = gw.execute.call_args.args[0]
        assert req.preferred_model is None

    def test_dict_result_serialized_to_json_text(self):
        gw = self._gw_with(result=_result({"score": 0.9}))
        with patch("core.ai_gateway.gateway.get_gateway", return_value=gw):
            out = self._adapter().generate("prompt")
        assert json.loads(out) == {"score": 0.9}

    def test_failed_result_raises_llm_client_error(self):
        # Evaluator-bound exception class: reload-safe (see test_evaluator).
        from core.evaluator import LLMClientError

        gw = self._gw_with(result=_result(None, method="failed", status="failed",
                                          error="all models down"))
        with patch("core.ai_gateway.gateway.get_gateway", return_value=gw):
            with pytest.raises(LLMClientError):
                self._adapter().generate("prompt")

    def test_deterministic_method_raises(self):
        """A deterministic fallback verdict is meaningless for a judge —
        it must surface as a failure so the sample is excluded."""
        from core.evaluator import LLMClientError

        gw = self._gw_with(result=_result({"keywords": []}, method="deterministic"))
        with patch("core.ai_gateway.gateway.get_gateway", return_value=gw):
            with pytest.raises(LLMClientError):
                self._adapter().generate("prompt")

    def test_empty_text_raises(self):
        from core.evaluator import LLMClientError

        gw = self._gw_with(result=_result("   "))
        with patch("core.ai_gateway.gateway.get_gateway", return_value=gw):
            with pytest.raises(LLMClientError):
                self._adapter().generate("prompt")

    def test_execute_exception_raises_llm_client_error(self):
        from core.evaluator import LLMClientError

        gw = self._gw_with(side_effect=RuntimeError("event loop gone"))
        with patch("core.ai_gateway.gateway.get_gateway", return_value=gw):
            with pytest.raises(LLMClientError):
                self._adapter().generate("prompt")

    def test_event_sink_called_on_success(self):
        gw = self._gw_with(result=_result("ok"))
        sink = MagicMock()
        with patch("core.ai_gateway.gateway.get_gateway", return_value=gw):
            self._adapter().generate("prompt", event_sink=sink)
        sink.assert_called_once()
        args = sink.call_args.args
        assert args[1] is True  # ok flag

    def test_event_sink_called_on_failure(self):
        from core.evaluator import LLMClientError

        gw = self._gw_with(result=_result(None, method="failed", status="failed",
                                          error="boom"))
        sink = MagicMock()
        with patch("core.ai_gateway.gateway.get_gateway", return_value=gw):
            with pytest.raises(LLMClientError):
                self._adapter().generate("prompt", event_sink=sink)
        sink.assert_called_once()
        assert sink.call_args.args[1] is False


# ---------------------------------------------------------------------------
# Provider readiness helper
# ---------------------------------------------------------------------------


class TestGatewayHasProviders:
    def test_true_when_providers_configured(self):
        from core.evaluator import _gateway_has_providers

        gw = MagicMock()
        gw.providers = {"gemini-3.5-flash-lite": MagicMock()}
        with patch("core.ai_gateway.gateway.get_gateway", return_value=gw):
            assert _gateway_has_providers() is True

    def test_false_when_no_providers(self):
        from core.evaluator import _gateway_has_providers

        gw = MagicMock()
        gw.providers = {}
        with patch("core.ai_gateway.gateway.get_gateway", return_value=gw):
            assert _gateway_has_providers() is False

    def test_false_when_gateway_import_fails(self):
        from core.evaluator import _gateway_has_providers

        with patch("core.ai_gateway.gateway.get_gateway", side_effect=RuntimeError("nope")):
            assert _gateway_has_providers() is False
