"""Tests for the summariser module — specifically parse_further_reading."""

import pytest
from core.summariser import parse_further_reading, format_articles_for_prompt


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
