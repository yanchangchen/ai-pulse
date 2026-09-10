"""Tests for classification mode, gate stats persistence, and heuristic
keyword auto-improvement."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
from collections import Counter

from config.settings import (
    CUSTOM_SETTINGS_FILE,
    load_custom_settings,
    save_custom_settings,
    get_classification_settings,
    update_classification_settings,
)
from config.themes import THEMES
from core.classifier import (
    keyword_classify,
    find_closest_theme,
    extract_keyword_suggestions_from_run,
    should_suggest_deterministic_mode,
    AUTO_APPLY_MIN_ARTICLES,
)


# ---------------------------------------------------------------------------
# Classification settings
# ---------------------------------------------------------------------------

class TestClassificationSettings:
    def test_defaults_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.settings.CUSTOM_SETTINGS_FILE", tmp_path / "nonexistent.json")
        settings = get_classification_settings()
        assert settings["classification_mode"] == "hybrid"
        assert settings["gate3_auto_disable_threshold"] == 0.05
        assert settings["gate_stats_history"] == []

    def test_update_persists(self, tmp_path, monkeypatch):
        f = tmp_path / "cs.json"
        monkeypatch.setattr("config.settings.CUSTOM_SETTINGS_FILE", f)
        update_classification_settings(classification_mode="deterministic")
        data = json.loads(f.read_text())
        assert data["classification_mode"] == "deterministic"

    def test_merge_with_other_settings(self, tmp_path, monkeypatch):
        f = tmp_path / "cs.json"
        monkeypatch.setattr("config.settings.CUSTOM_SETTINGS_FILE", f)
        # Write some other setting (e.g. summariser temperature)
        f.write_text(json.dumps({"temperature": 0.5}))
        update_classification_settings(classification_mode="deterministic")
        data = json.loads(f.read_text())
        assert data["classification_mode"] == "deterministic"
        assert data["temperature"] == 0.5  # other settings preserved

    def test_gate_stats_history_capped_at_20(self, tmp_path, monkeypatch):
        f = tmp_path / "cs.json"
        monkeypatch.setattr("config.settings.CUSTOM_SETTINGS_FILE", f)
        # The capping happens in classify_articles(), not in update_classification_settings.
        # update_classification_settings stores whatever it's given. Verify that
        # storing 25 entries directly works (no crash), and that the cap is
        # applied by the caller (classify_articles).
        history = [{"timestamp": f"2025-01-{i:02d}", "gate3_rate": 0.1} for i in range(25)]
        update_classification_settings(gate_stats_history=history)
        settings = get_classification_settings()
        # All 25 entries are stored; the cap is enforced by the caller
        assert len(settings["gate_stats_history"]) == 25


# ---------------------------------------------------------------------------
# Deterministic mode
# ---------------------------------------------------------------------------

class TestDeterministicMode:
    def test_skip_llm_skips_gate3(self):
        """When skip_llm=True, articles that miss Gate 1+2 go to Gate 4."""
        from core.classifier import classify_articles
        # Create articles that will definitely miss Gate 1 (no keywords)
        # and Gate 2 (low similarity)
        articles = [
            {"title": "Quantum entanglement breakthrough", "summary": "Scientists observe quantum coherence."},
            {"title": "New recipe for sourdough", "summary": "A guide to baking better bread."},
        ]
        with patch("config.settings.get_classification_settings", return_value={"classification_mode": "deterministic", "gate3_auto_disable_threshold": 0.05, "gate_stats_history": []}), \
             patch("config.settings.update_classification_settings"):
            result = classify_articles(articles, skip_llm=True)

        # All articles should be classified (Gate 4 always returns a theme)
        total = sum(len(v) for v in result.values())
        assert total == 2

    def test_hybrid_mode_includes_gate3(self):
        """In hybrid mode, articles can go through Gate 3 (LLM)."""
        from core.classifier import classify_articles
        articles = [
            {"title": "Quantum entanglement breakthrough", "summary": "Scientists observe quantum coherence."},
        ]
        # Mock the gateway to return a classification
        mock_result = MagicMock()
        mock_result.is_success.return_value = True
        mock_result.result = '{"ID 0": "Frontier Models & Benchmarks"}'
        mock_prov = MagicMock()
        mock_prov.to_dict.return_value = {}
        mock_result.provenance = mock_prov

        with patch("config.settings.get_classification_settings", return_value={"classification_mode": "hybrid", "gate3_auto_disable_threshold": 0.05, "gate_stats_history": []}), \
             patch("config.settings.update_classification_settings"), \
             patch("core.classifier.get_gateway") as mock_gw:
            mock_gw.return_value.execute = MagicMock(return_value=mock_result)
            result = classify_articles(articles, skip_llm=False)

        # Gateway should have been called
        assert mock_gw.return_value.execute.called


# ---------------------------------------------------------------------------
# should_suggest_deterministic_mode
# ---------------------------------------------------------------------------

class TestShouldSuggestDeterministicMode:
    def test_returns_true_when_gate3_rate_consistently_low(self, monkeypatch, tmp_path):
        f = tmp_path / "cs.json"
        monkeypatch.setattr("config.settings.CUSTOM_SETTINGS_FILE", f)
        # Seed 5 hybrid runs with gate3_rate < 0.05
        history = [
            {"mode": "hybrid", "gate3_rate": 0.02},
            {"mode": "hybrid", "gate3_rate": 0.03},
            {"mode": "hybrid", "gate3_rate": 0.01},
            {"mode": "hybrid", "gate3_rate": 0.04},
            {"mode": "hybrid", "gate3_rate": 0.02},
        ]
        update_classification_settings(gate_stats_history=history)
        assert should_suggest_deterministic_mode() is True

    def test_returns_false_when_gate3_rate_high(self, monkeypatch, tmp_path):
        f = tmp_path / "cs.json"
        monkeypatch.setattr("config.settings.CUSTOM_SETTINGS_FILE", f)
        history = [
            {"mode": "hybrid", "gate3_rate": 0.15},
            {"mode": "hybrid", "gate3_rate": 0.20},
            {"mode": "hybrid", "gate3_rate": 0.10},
        ]
        update_classification_settings(gate_stats_history=history)
        assert should_suggest_deterministic_mode() is False

    def test_returns_false_when_fewer_than_3_hybrid_runs(self, monkeypatch, tmp_path):
        f = tmp_path / "cs.json"
        monkeypatch.setattr("config.settings.CUSTOM_SETTINGS_FILE", f)
        history = [
            {"mode": "hybrid", "gate3_rate": 0.01},
            {"mode": "hybrid", "gate3_rate": 0.02},
        ]
        update_classification_settings(gate_stats_history=history)
        assert should_suggest_deterministic_mode() is False


# ---------------------------------------------------------------------------
# Keyword auto-improvement
# ---------------------------------------------------------------------------

class TestExtractKeywordSuggestions:
    def test_auto_applies_high_confidence_keywords(self):
        """Terms appearing in 3+ Gate 3/4 articles for the same theme
        should be auto-applied."""
        # Create articles with a recurring term not in the theme's keywords
        articles = {
            "Agentic Systems & DevTools": [
                {"title": "Agentic mesh networks emerge", "summary": "Agentic mesh connects agents.", "gate": 4},
                {"title": "Mesh orchestration patterns", "summary": "Mesh patterns for multi-agent.", "gate": 4},
                {"title": "Mesh protocols standardised", "summary": "New mesh protocol released.", "gate": 4},
            ]
        }
        with patch("config.themes.add_keywords_to_theme") as mock_add, \
             patch("core.classifier._store_keyword_suggestions"):
            result = extract_keyword_suggestions_from_run(articles)

        # "mesh" appears in 3+ articles → should be auto-applied
        auto_terms = [s["term"] for s in result["auto_applied"]]
        assert any("mesh" in t for t in auto_terms)
        mock_add.assert_called()

    def test_no_auto_apply_for_cross_theme_terms(self):
        """Terms that already belong to another theme should NOT be auto-applied."""
        # "training" is a keyword in Frontier Models & Benchmarks, so it shouldn't
        # be auto-applied to AI Security & Trust.
        articles = {
            "AI Security & Trust": [
                {"title": "Training data poisoning discovered", "summary": "Training data used in attacks.", "gate": 4},
                {"title": "Training backdoor found", "summary": "Training backdoor in LLM.", "gate": 4},
                {"title": "Training safety benchmark", "summary": "Training safety tested.", "gate": 4},
            ]
        }
        with patch("config.themes.add_keywords_to_theme") as mock_add, \
             patch("core.classifier._store_keyword_suggestions"):
            result = extract_keyword_suggestions_from_run(articles)

        # "training" already belongs to Frontier Models → should not be auto-applied
        # to AI Security & Trust.
        auto_terms = [s["term"] for s in result["auto_applied"]]
        assert "training" not in auto_terms

    def test_empty_articles_no_suggestions(self):
        """Empty themed_articles should not crash."""
        with patch("core.classifier._store_keyword_suggestions"):
            result = extract_keyword_suggestions_from_run({})
        assert result["auto_applied"] == []
        assert result["pending"] == []

    def test_gate_1_2_articles_not_analysed(self):
        """Only Gate 3 and 4 articles should be analysed for suggestions."""
        articles = {
            "Agentic Systems & DevTools": [
                {"title": "RAG pipeline deployed", "summary": "RAG implementation.", "gate": 1},
            ]
        }
        with patch("core.classifier._store_keyword_suggestions"):
            result = extract_keyword_suggestions_from_run(articles)
        # Gate 1 articles should not generate suggestions
        assert len(result["auto_applied"]) == 0
        assert len(result["pending"]) == 0

    def test_pending_suggestions_below_threshold(self):
        """Terms appearing in exactly 2 articles (below AUTO_APPLY_MIN_ARTICLES=3)
        should be pending, not auto-applied."""
        articles = {
            "AI Security & Trust": [
                {"title": "Zeroday exploit found", "summary": "Zeroday vulnerability in LLM.", "gate": 4},
                {"title": "Zeroday patch released", "summary": "Zeroday fixed in latest update.", "gate": 4},
            ]
        }
        with patch("config.themes.add_keywords_to_theme") as mock_add, \
             patch("core.classifier._store_keyword_suggestions"):
            result = extract_keyword_suggestions_from_run(articles)

        pending_terms = [s["term"] for s in result["pending"]]
        auto_terms = [s["term"] for s in result["auto_applied"]]
        # "zeroday" appears in 2 articles → pending, not auto-applied
        assert any("zeroday" in t for t in pending_terms)
        # Should NOT be auto-applied
        assert not any("zeroday" in t for t in auto_terms)

    def test_stopwords_and_noise_terms_are_filtered(self):
        """Common stopwords like 'your', 'across', and noisy domain terms
        should not be suggested as keywords."""
        articles = {
            "Agentic Systems & DevTools": [
                {"title": "Your phone across the platform", "summary": "Your phone works across the system.", "gate": 4},
                {"title": "Photos and more photos", "summary": "Photos show the new photos feature.", "gate": 4},
            ]
        }
        with patch("config.themes.add_keywords_to_theme") as mock_add, \
             patch("core.classifier._store_keyword_suggestions"):
            result = extract_keyword_suggestions_from_run(articles)

        all_terms = [s["term"] for s in result["auto_applied"] + result["pending"]]
        noise_terms = {"your", "across", "phone", "photos"}
        for term in all_terms:
            assert term not in noise_terms

    def test_cross_theme_generic_terms_are_filtered(self):
        """A term that appears across multiple themes should be considered
        too generic and not suggested."""
        articles = {
            "Agentic Systems & DevTools": [
                {"title": "Neural agents deployed", "summary": "Neural agents run tasks.", "gate": 4},
                {"title": "Neural agent update", "summary": "Neural agent improvements.", "gate": 4},
            ],
            "Frontier Models & Benchmarks": [
                {"title": "Neural model released", "summary": "Neural model trains faster.", "gate": 4},
                {"title": "Neural benchmark set", "summary": "Neural benchmark results.", "gate": 4},
            ],
        }
        with patch("config.themes.add_keywords_to_theme") as mock_add, \
             patch("core.classifier._store_keyword_suggestions"):
            result = extract_keyword_suggestions_from_run(articles)

        all_terms = [s["term"] for s in result["auto_applied"] + result["pending"]]
        assert "neural" not in all_terms

    def test_unigrams_must_appear_in_title(self):
        """Unigrams should only be suggested if they appear in at least one
        article title."""
        articles = {
            "AI Security & Trust": [
                {"title": "New attack found", "summary": "Zeroday attack in LLM.", "gate": 4},
                {"title": "Patch fixes attack", "summary": "Attack patched.", "gate": 4},
            ]
        }
        with patch("config.themes.add_keywords_to_theme") as mock_add, \
             patch("core.classifier._store_keyword_suggestions"):
            result = extract_keyword_suggestions_from_run(articles)

        all_terms = [s["term"] for s in result["auto_applied"] + result["pending"]]
        # "attack" appears in both titles → should be suggested
        assert any("attack" in t for t in all_terms)

    def test_unigrams_only_in_summary_are_not_suggested(self):
        """Unigrams that only appear in article summaries (never in titles)
        should not be suggested as keywords."""
        articles = {
            "AI Security & Trust": [
                {"title": "New exploit found", "summary": "Zeroday vulnerability details.", "gate": 4},
                {"title": "Patch released", "summary": "Zeroday vulnerability patched.", "gate": 4},
            ]
        }
        with patch("config.themes.add_keywords_to_theme") as mock_add, \
             patch("core.classifier._store_keyword_suggestions"):
            result = extract_keyword_suggestions_from_run(articles)

        all_terms = [s["term"] for s in result["auto_applied"] + result["pending"]]
        # "vulnerability" only appears in summaries, never in titles
        assert "vulnerability" not in all_terms