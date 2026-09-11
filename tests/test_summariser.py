"""Tests for the summariser module — specifically parse_further_reading."""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.summariser import (
    parse_further_reading,
    format_articles_for_prompt,
    _summary_contains_failure_text,
)


class TestParseFurtherReading:
    """Test the further-reading parser."""

    def test_standard_pipe_format(self):
        text = "- Article Title | Source | https://example.com | Worth reading because X"
        result = parse_further_reading(text)
        assert len(result) == 1
        assert result[0]['title'] == "Article Title"
        assert result[0]['source'] == "Source"
        assert result[0]['url'] == "https://example.com"
        assert result[0]['reason'] == "Worth reading because X"

    def test_multiple_entries(self):
        text = (
            "- Title 1 | Source A | https://a.com | Reason A\n"
            "- Title 2 | Source B | https://b.com | Reason B\n"
            "- Title 3 | Source C | https://c.com | Reason C"
        )
        result = parse_further_reading(text)
        assert len(result) == 3

    def test_asterisk_bullets(self):
        text = "* Article | Source | https://url.com | Great read"
        result = parse_further_reading(text)
        assert len(result) == 1
        assert result[0]['title'] == "Article"

    def test_no_reason_field(self):
        text = "- Title | Source | https://url.com"
        result = parse_further_reading(text)
        assert len(result) == 1
        assert result[0]['reason'] == ""

    def test_empty_string(self):
        assert parse_further_reading("") == []

    def test_no_pipe_lines_skipped(self):
        text = "This is just a sentence with no pipes."
        result = parse_further_reading(text)
        assert result == []

    def test_blank_lines_ignored(self):
        text = "\n\n- Title | Source | https://url.com | Reason\n\n"
        result = parse_further_reading(text)
        assert len(result) == 1


class TestFormatArticlesForPrompt:
    """Test the article formatter + char-budget truncation guard."""

    def _make_article(self, i: int) -> dict:
        return {
            "title": f"Article {i} title",
            "summary": "x" * 300,  # hits the 300-char per-article cap
            "source_name": f"Source {i}",
            "link": f"https://example.com/{i}",
        }

    def test_no_budget_returns_all(self):
        articles = [self._make_article(i) for i in range(5)]
        out = format_articles_for_prompt(articles, char_budget=None)
        # All 5 article numbers should appear in the output.  The first
        # one is at offset 0 (no leading newline), the rest are
        # newline-prefixed by the trailing blank line of the previous block.
        assert out.startswith("1. ")
        for n in range(2, 6):
            assert f"\n{n}. " in out
        assert "\n6. " not in out

    def test_small_budget_truncates_tail(self):
        articles = [self._make_article(i) for i in range(20)]
        # 800 chars is far too small for 20 articles; expect a short prefix.
        out = format_articles_for_prompt(articles, char_budget=800)
        # Should keep only the first few articles.
        assert "1. " in out
        assert "20. " not in out
        assert len(out) <= 800

    def test_budget_zero_returns_empty(self):
        articles = [self._make_article(i) for i in range(3)]
        out = format_articles_for_prompt(articles, char_budget=0)
        assert out == ""

    def test_budget_just_enough_for_one_article(self):
        articles = [self._make_article(i) for i in range(3)]
        # A single article is ~470 chars of formatted text (1. title\n
        #   Source: x\n   Summary: 300 chars\n   URL: x\n).
        first_block = format_articles_for_prompt([articles[0]], char_budget=None)
        out = format_articles_for_prompt(articles, char_budget=len(first_block))
        assert "1. " in out
        assert "2. " not in out


class TestSummaryContainsFailureText:
    def test_detects_known_failure_strings(self):
        summary = {
            "what_is_happening": "Unable to generate summary.",
            "engineering_tradeoffs": "No engineering tradeoffs analyzed.",
            "product_impact": "Some real content here.",
            "why_it_matters": "This matters.",
        }
        assert _summary_contains_failure_text(summary) is True

    def test_no_false_positives_for_valid_summary(self):
        summary = {
            "what_is_happening": "Several open-weight models were released this week.",
            "engineering_tradeoffs": "Latency improved at the cost of accuracy.",
            "product_impact": "Enterprise adoption accelerated.",
            "why_it_matters": "Competitive pressure is reshaping pricing.",
        }
        assert _summary_contains_failure_text(summary) is False


class TestGenerateThemeSummaryGatewayContentFallback:
    """If the gateway returns text containing failure phrases, the summariser
    should fall back to the extractive engine."""

    @pytest.fixture
    def articles(self):
        return [
            {
                "title": "Model A updates",
                "summary": "Model A introduces feature X.",
                "source_name": "Source A",
                "link": "https://example.com/a",
            },
            {
                "title": "Model B released",
                "summary": "Model B targets enterprise use.",
                "source_name": "Source B",
                "link": "https://example.com/b",
            },
            {
                "title": "Model C benchmarked",
                "summary": "Model C tops the new benchmark.",
                "source_name": "Source C",
                "link": "https://example.com/c",
            },
        ]

    def test_fallback_when_llm_returns_failure_text(self, articles):
        from core.summariser import generate_theme_summary_gateway
        from core.ai_gateway import AITaskResult

        # Build a fake gateway result whose content is a failure string
        fake_result = MagicMock(spec=AITaskResult)
        fake_result.is_success.return_value = True
        fake_result.result = """## 1. WHAT IS HAPPENING
Unable to generate summary.

## 2. ENGINEERING TRADEOFFS
No engineering tradeoffs analyzed.

## 3. PRODUCT IMPACT
No product impact analyzed.

## 4. ACTIONABLE WATCHLIST
- Nothing

## 5. STRATEGIC FURTHER READING
- Nothing
"""
        fake_prov = MagicMock()
        fake_prov.method = "llm"
        fake_prov.provider = "ollama"
        fake_prov.model = "nemotron-3-super"
        fake_prov.task = "summarise"
        fake_prov.latency_ms = 123
        fake_prov.attempts = 1
        fake_prov.fallback_used = False
        fake_prov.to_dict.return_value = {}
        fake_result.provenance = fake_prov

        fake_gateway = MagicMock()
        fake_gateway.execute = AsyncMock(return_value=fake_result)

        with patch("core.summariser.get_gateway", return_value=fake_gateway):
            summary = asyncio.run(
                generate_theme_summary_gateway("Frontier Models & Benchmarks", articles)
            )

        # Should have fallen back to the non-LLM extractive summariser
        assert summary.get("_source") == "extractive_fallback"
        assert "Unable to generate summary" not in (summary.get("what_is_happening") or "")

    def test_no_fallback_for_valid_llm_output(self, articles):
        from core.summariser import generate_theme_summary_gateway
        from core.ai_gateway import AITaskResult

        fake_result = MagicMock(spec=AITaskResult)
        fake_result.is_success.return_value = True
        fake_result.result = """## 1. WHAT IS HAPPENING
Open-source models are advancing rapidly.

## 2. ENGINEERING TRADEOFFS
Architects must weigh latency against accuracy.

## 3. PRODUCT IMPACT
Enterprise adoption is accelerating.

## 4. ACTIONABLE WATCHLIST
- Watch the next release.

## 5. STRATEGIC FURTHER READING
- Article | Source | https://example.com
"""
        fake_prov = MagicMock()
        fake_prov.method = "llm"
        fake_prov.provider = "ollama"
        fake_prov.model = "nemotron-3-super"
        fake_prov.task = "summarise"
        fake_prov.latency_ms = 123
        fake_prov.attempts = 1
        fake_prov.fallback_used = False
        fake_prov.to_dict.return_value = {}
        fake_result.provenance = fake_prov

        fake_gateway = MagicMock()
        fake_gateway.execute = AsyncMock(return_value=fake_result)

        with patch("core.summariser.get_gateway", return_value=fake_gateway):
            summary = asyncio.run(
                generate_theme_summary_gateway("Frontier Models & Benchmarks", articles)
            )

        # Should keep the LLM output, not the extractive fallback
        assert summary.get("_source") != "extractive_fallback"
        assert "Open-source models" in (summary.get("what_is_happening") or "")