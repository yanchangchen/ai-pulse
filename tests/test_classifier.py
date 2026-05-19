"""Tests for the weighted keyword classifier."""

import pytest
from core.classifier import keyword_classify, find_closest_theme


class TestKeywordClassify:
    """Test weighted keyword classification."""

    def test_strong_rag_signal(self):
        """RAG and agents should strongly classify as Agentic Systems & DevTools."""
        result = keyword_classify("Building RAG agents with LangChain", "This guide covers agentic RAG pipelines.")
        assert result == "Agentic Systems & DevTools"

    def test_model_release(self):
        """Model release keywords should classify as Frontier Models & Benchmarks."""
        result = keyword_classify("GPT-5 released with new benchmark results", "OpenAI announces a new model with multimodal reasoning.")
        assert result == "Frontier Models & Benchmarks"

    def test_infrastructure_gpu(self):
        """GPU and chip keywords should classify as Hardware, Compute & LLMOps."""
        result = keyword_classify("NVIDIA releases new GPU for data center inference", "The chip reduces latency by 50%.")
        assert result == "Hardware, Compute & LLMOps"

    def test_business_funding(self):
        """Funding and acquisition keywords should classify as Enterprise Strategy & ROI."""
        result = keyword_classify("AI startup raises $500M in funding", "The valuation reached $5B after the latest investor deal.")
        assert result == "Enterprise Strategy & ROI"

    def test_policy_regulation(self):
        """Regulation keywords should classify as Governance, Safety & Policy."""
        result = keyword_classify("EU AI Act enters enforcement phase", "New legislation requires compliance from all AI companies.")
        assert result == "Governance, Safety & Policy"

    def test_no_match_returns_none(self):
        """Completely unrelated text should return None."""
        result = keyword_classify("Weather forecast for today", "It will be sunny and warm.")
        assert result is None

    def test_weighted_scoring_resolves_ambiguity(self):
        """When keywords from multiple themes appear, the higher-weighted theme should win."""
        # "OpenAI" has weight 1 in Business, but "model release" has weight 3 in Models
        result = keyword_classify("OpenAI model release", "A new frontier model with benchmark results.")
        assert result == "Frontier Models & Benchmarks"

    def test_case_insensitive(self):
        """Classification should be case-insensitive."""
        result = keyword_classify("rag agents langchain", "embedding retrieval pipeline")
        assert result == "Agentic Systems & DevTools"


class TestFindClosestTheme:
    """Test find_closest_theme fallback logic."""

    def test_sub_keyword_match(self):
        """Should match keyword even if it's not a full word bound keyword or in less structured text."""
        # 'regulation' is a Governance, Safety & Policy keyword, but even with relaxed match:
        result = find_closest_theme("some regulator news", "policy and rules discussion")
        assert result == "Governance, Safety & Policy"

    def test_theme_word_overlap_match(self):
        """Should match word from theme name when no other keywords match."""
        result = find_closest_theme("news on compute", "talking about centers and setups")
        assert result == "Hardware, Compute & LLMOps"

    def test_ultimate_default(self):
        """Should fallback to Agentic Systems & DevTools when absolutely nothing matches."""
        result = find_closest_theme("completely random text", "weather is nice today")
        assert result == "Agentic Systems & DevTools"

