"""
Unit tests for non-LLM Extractive Summarizer.
"""

import pytest
from core.non_llm_summariser import (
    lexrank_sentences,
    luhn_sentences,
    extract_keyphrases,
    generate_non_llm_theme_summary,
)


def test_lexrank_sentences():
    sentences = [
        "OpenAI announced GPT-5 with major reasoning improvements across all technical benchmarks.",
        "The model demonstrates state-of-the-art accuracy in coding and mathematics.",
        "Engineers report massive reduction in hallucinations during complex task execution.",
        "A completely unrelated sentence about making morning coffee and baking fresh bread."
    ]
    ranked = lexrank_sentences(sentences, top_n=2)
    assert len(ranked) == 2
    assert isinstance(ranked, list)


def test_luhn_sentences():
    sentences = [
        "The new GPU cluster architecture optimizes memory throughput and reduces latency by 40%.",
        "Weather forecast predicts mild sunshine and light rain across the valley tomorrow.",
        "Deep Learning weights and open-source models improve API integration efficiency."
    ]
    keywords = ["gpu", "architecture", "latency", "weights", "api"]
    scored = luhn_sentences(sentences, keywords, top_n=2)
    assert len(scored) > 0
    assert any("gpu" in s.lower() or "latency" in s.lower() for s in scored)


def test_extract_keyphrases():
    text = "Artificial intelligence transformer models scale up GPU clusters for enterprise automation."
    keyphrases = extract_keyphrases(text, top_n=3)
    assert len(keyphrases) > 0
    assert isinstance(keyphrases, list)


def test_generate_non_llm_theme_summary():
    articles = [
        {
            "title": "Anthropic Releases Claude 3.7 Sonnet for Hybrid Reasoning",
            "summary": "Anthropic introduced Claude 3.7 Sonnet featuring hybrid reasoning architecture for complex coding tasks.",
            "source_name": "Anthropic Engineering",
            "link": "https://anthropic.com/claude-3-7"
        },
        {
            "title": "DeepMind Benchmark Analysis on Reasoning Performance",
            "summary": "Google DeepMind published new benchmarks comparing reasoning latency and memory consumption.",
            "source_name": "Google DeepMind",
            "link": "https://deepmind.google/research"
        }
    ]

    summary = generate_non_llm_theme_summary("Agentic Systems & DevTools", articles)

    assert "what_is_happening" in summary
    assert "engineering_tradeoffs" in summary
    assert "product_impact" in summary
    assert "why_it_matters" in summary
    assert "what_to_watch" in summary
    assert "further_reading" in summary

    assert len(summary["what_is_happening"]) > 10
    assert "• **" in summary["what_is_happening"]


def test_best_sentence_for_article():
    from core.non_llm_summariser import _best_sentence_for_article

    article_with_summary = {
        "title": "New Frontier Model Released",
        "summary": "The research team published a 70B parameter model. It outperforms GPT-4 on coding benchmarks."
    }
    best_sent = _best_sentence_for_article(article_with_summary)
    assert "model" in best_sent.lower() or "gpt-4" in best_sent.lower()

    article_title_only = {
        "title": "Title Only Article Announcement",
        "summary": ""
    }
    title_sent = _best_sentence_for_article(article_title_only)
    assert "Title Only Article Announcement" in title_sent

