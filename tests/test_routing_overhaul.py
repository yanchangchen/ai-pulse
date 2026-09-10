"""Tests for summarisation routing overhaul and the improved gateway
deterministic fallback."""

import pytest
from unittest.mock import MagicMock, patch

from core.ai_gateway.gateway import ModelGateway
from core.ai_gateway.contracts import TaskType


# ---------------------------------------------------------------------------
# Routing policy verification
# ---------------------------------------------------------------------------

class TestRoutingPolicies:
    def test_summarise_primary_is_ollama(self):
        """Summarise tasks should default to Ollama (nemotron) as primary."""
        gw = ModelGateway()
        policy = gw._get_routing_policy(TaskType.SUMMARISE)
        assert policy["primary"] == "nemotron-3-super"

    def test_synthesise_primary_is_ollama(self):
        """Synthesise tasks should default to Ollama as primary."""
        gw = ModelGateway()
        policy = gw._get_routing_policy(TaskType.SYNTHESISE)
        assert policy["primary"] == "nemotron-3-super"

    def test_project_primary_is_ollama(self):
        """Project tasks should default to Ollama as primary."""
        gw = ModelGateway()
        policy = gw._get_routing_policy(TaskType.PROJECT)
        assert policy["primary"] == "nemotron-3-super"

    def test_categorise_primary_is_gemini(self):
        """Categorise tasks should keep Gemini flash-lite as primary (lightweight)."""
        gw = ModelGateway()
        policy = gw._get_routing_policy(TaskType.CATEGORISE)
        assert policy["primary"] == "gemini-3.5-flash-lite"

    def test_extract_primary_is_gemini(self):
        """Extract tasks should keep Gemini flash-lite as primary (lightweight)."""
        gw = ModelGateway()
        policy = gw._get_routing_policy(TaskType.EXTRACT)
        assert policy["primary"] == "gemini-3.5-flash-lite"

    def test_summarise_fallback_includes_gemini(self):
        """Gemini should appear in the fallback chain for summarise."""
        gw = ModelGateway()
        policy = gw._get_routing_policy(TaskType.SUMMARISE)
        assert "gemini-3.6-flash" in policy["fallback"]
        assert "gemini-3.5-flash" in policy["fallback"]

    def test_summarise_ollama_before_gemini(self):
        """Ollama models should come before Gemini in the summarise fallback chain."""
        gw = ModelGateway()
        policy = gw._get_routing_policy(TaskType.SUMMARISE)
        fallback = policy["fallback"]
        ollama_positions = [i for i, m in enumerate(fallback) if "gemini" not in m]
        gemini_positions = [i for i, m in enumerate(fallback) if "gemini" in m]
        if ollama_positions and gemini_positions:
            assert min(ollama_positions) < min(gemini_positions)


# ---------------------------------------------------------------------------
# extractive_summarise_from_text
# ---------------------------------------------------------------------------

class TestExtractiveSummariseFromText:
    def test_returns_expected_keys(self):
        from core.non_llm_summariser import extractive_summarise_from_text
        text = "AI models are getting larger. Training costs are rising. Enterprise adoption is accelerating. New benchmarks show improved performance. GPU supply remains constrained."
        result = extractive_summarise_from_text(text)
        expected_keys = {"what_is_happening", "engineering_tradeoffs", "product_impact", "why_it_matters", "what_to_watch", "further_reading", "method"}
        assert set(result.keys()) == expected_keys

    def test_method_is_v2(self):
        from core.non_llm_summariser import extractive_summarise_from_text
        result = extractive_summarise_from_text("Some input text about AI developments.")
        assert result["method"] == "non-llm-extractive-v2"

    def test_empty_text_returns_fallback(self):
        from core.non_llm_summariser import extractive_summarise_from_text
        result = extractive_summarise_from_text("")
        assert "No content available" in result["what_is_happening"]

    def test_long_text_produces_substantive_output(self):
        from core.non_llm_summariser import extractive_summarise_from_text
        text = (
            "OpenAI released GPT-5 with significant improvements in reasoning. "
            "The model uses a new transformer architecture with mixture of experts. "
            "Enterprise customers report 40% cost reduction in production deployments. "
            "GPU memory requirements have doubled compared to GPT-4. "
            "Benchmark results show GPT-5 outperforms Claude 4 on MMLU by 15 points. "
            "API pricing is set at $10 per million input tokens. "
            "The model supports 1M token context windows. "
            "Safety evaluations indicate improved refusal rates on harmful prompts."
        )
        result = extractive_summarise_from_text(text, max_sentences=5)
        # what_is_happening should contain substantive content
        assert len(result["what_is_happening"]) > 20
