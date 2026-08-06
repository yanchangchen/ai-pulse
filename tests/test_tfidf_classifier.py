"""
Unit tests for core/tfidf_classifier.py and 4-pass waterfall classification gate tracking.
"""

import pytest
from core.tfidf_classifier import TfidfThemeClassifier, tfidf_classify
from core.classifier import classify_articles, get_latest_gate_stats


def test_tfidf_classifier_initialization():
    classifier = TfidfThemeClassifier()
    assert len(classifier.themes) == 7
    assert len(classifier.theme_tfidf_norms) == 7
    assert len(classifier.vocabulary) > 0


def test_tfidf_classify_software_engineering():
    best_theme, score, score_dict = tfidf_classify(
        "AI Coding Assistant in IDE",
        "Developer workflow refactoring and pair programming with AI code completions",
    )
    assert best_theme == "AI-Assisted Software Engineering"
    assert score > 0.05
    assert isinstance(score_dict, dict)
    assert len(score_dict) == 7


def test_tfidf_classify_security():
    best_theme, score, _ = tfidf_classify(
        "Indirect Prompt Injection Attack",
        "Red teaming LLM guardrails against jailbreaks and exfiltration",
    )
    assert best_theme == "AI Security & Trust"
    assert score > 0.05


def test_tfidf_classify_hardware():
    best_theme, score, _ = tfidf_classify(
        "NVIDIA Blackwell GPU Supercluster",
        "Data center power demand, semiconductor bandwidth, and TPU inference efficiency",
    )
    assert best_theme == "Hardware, Compute & LLMOps"
    assert score > 0.05


def test_tfidf_classify_empty_input():
    theme, score, score_dict = tfidf_classify("", "")
    assert theme is None
    assert score == 0.0
    assert all(v == 0.0 for v in score_dict.values())


def test_classify_articles_waterfall_gate_tracking():
    articles = [
        # Pass 1: Keyword hit ("Cursor")
        {"title": "Cursor AI IDE release", "summary": "New features for developer experience"},
        # Pass 2: TF-IDF hit (no exact weighted keyword, but TF-IDF vector match for hardware)
        {"title": "Data center power and chip bandwidth", "summary": "Infrastructure scaling and efficiency"},
    ]
    themed = classify_articles(articles)
    stats = get_latest_gate_stats()

    assert stats["total"] == 2
    assert stats["gate_1_keyword"] + stats["gate_2_tfidf"] == 2
    assert sum(len(v) for v in themed.values()) == 2
