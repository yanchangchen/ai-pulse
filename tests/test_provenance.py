"""Tests for the summary provenance chip in core/provenance.py."""

import re

from core.provenance import (
    _classify,
    _format_label,
    render_provenance_chip,
    strip_fallback_banner,
)


def _has_class(html: str, class_name: str) -> bool:
    return f'class="{class_name}"' in html


def _has_text(html: str, needle: str) -> bool:
    return needle in html


class TestClassify:
    def test_known_exact_source(self):
        assert _classify("extractive_fallback") == "extractive_fallback"
        assert _classify("ollama:error") == "ollama:error"
        assert _classify("ollama:no_articles") == "ollama:no_articles"
        assert _classify("ollama:limited_coverage") == "ollama:limited_coverage"
        assert _classify("ollama:no_new_articles_skip") == "ollama:no_new_articles_skip"

    def test_ollama_prefix(self):
        assert _classify("ollama:qwen3-coder:30b") == "ollama"
        assert _classify("ollama:minimax-m3:cloud") == "ollama"

    def test_gemini_prefix(self):
        assert _classify("gemini:gemini-2.0-flash") == "gemini"
        assert _classify("gemini:gemini-1.5-pro") == "gemini"

    def test_unknown_falls_back(self):
        # Unknown tokens are treated as fallback so the user always sees
        # the safest label.
        assert _classify("something_unexpected") == "extractive_fallback"

    def test_none_falls_back(self):
        assert _classify(None) == "extractive_fallback"


class TestFormatLabel:
    def test_known_exact_label(self):
        assert _format_label("extractive_fallback") == "Non-LLM fallback"
        assert _format_label("ollama:error") == "LLM error"

    def test_ollama_model_unwraps(self):
        assert _format_label("ollama:qwen3-coder:30b") == "Ollama · qwen3-coder:30b"

    def test_gemini_model_unwraps(self):
        assert _format_label("gemini:gemini-2.0-flash") == "Gemini · gemini-2.0-flash"

    def test_none_label(self):
        assert _format_label(None) == "Unknown source"


class TestRenderProvenanceChip:
    def test_extractive_fallback_emits_warning_color(self):
        chip = render_provenance_chip({"_source": "extractive_fallback"})
        assert _has_text(chip, "Non-LLM fallback")
        assert "🛟" in chip  # fallback icon
        # warning amber palette
        assert "#8a4b00" in chip
        assert "#fff4e5" in chip

    def test_ollama_synthesis_emits_ollama_color(self):
        chip = render_provenance_chip({
            "_source": "ollama:qwen3-coder:30b",
            "_generation_log": {"model": "qwen3-coder:30b", "article_count": 12},
        })
        assert "Ollama" in chip
        assert "qwen3-coder:30b" in chip
        assert "🧠" in chip
        assert "#0b4a8a" in chip

    def test_gemini_synthesis_emits_gemini_color(self):
        chip = render_provenance_chip({
            "_source": "gemini:gemini-2.0-flash",
            "_generation_log": {"model": "gemini-2.0-flash", "article_count": 8},
        })
        assert "Gemini" in chip
        assert "🛟" not in chip  # not the fallback icon
        assert "✨" in chip
        assert "#3a1d8a" in chip

    def test_tooltip_contains_log_fields(self):
        chip = render_provenance_chip(
            {
                "_source": "ollama:qwen3-coder:30b",
                "_generation_log": {
                    "model": "qwen3-coder:30b",
                    "article_count": 42,
                    "note": "live_synthesis",
                    "generated_at": "2026-08-18T10:34:00",
                },
            },
            tooltip=True,
        )
        # Title attribute should carry the log fields.
        title_match = re.search(r'title="([^"]+)"', chip)
        assert title_match is not None
        title = title_match.group(1)
        assert "model: qwen3-coder:30b" in title
        assert "articles: 42" in title
        assert "note: live_synthesis" in title
        assert "at: 2026-08-18T10:34:00" in title

    def test_tooltip_omitted_when_disabled(self):
        chip = render_provenance_chip(
            {"_source": "ollama:qwen3-coder:30b", "_generation_log": {"model": "qwen3-coder:30b"}},
            tooltip=False,
        )
        assert "title=" not in chip

    def test_missing_source_treated_as_fallback(self):
        chip = render_provenance_chip({})
        # No _source key → styled as extractive_fallback palette + "Unknown
        # source" label so the chip is still informative.
        assert "Unknown source" in chip
        assert "#fff4e5" in chip  # fallback background colour

    def test_error_in_log_appears_in_tooltip(self):
        chip = render_provenance_chip({
            "_source": "ollama:error",
            "_generation_log": {"error": "timeout after 25s"},
        })
        title_match = re.search(r'title="([^"]+)"', chip)
        assert title_match is not None
        assert "error: timeout after 25s" in title_match.group(1)


class TestStripFallbackBanner:
    UPPERCASE = (
        "<em>⚠️ Non-LLM Extractive Summary: Live LLM synthesis failed "
        "(empty response / timeout), so this brief was compiled deterministically "
        "from the article pool using LexRank & Luhn extractive NLP.</em>\n\n"
        "The actual signal body follows here."
    )

    LOWERCASE = (
        "<em>⚠️ non-llm extractive summary: live llm synthesis failed "
        "(empty response / timeout), so this brief was compiled deterministically "
        "from the article pool using lexrank & luhn extractive nlp.</em>\n\n"
        "Body text after the banner."
    )

    def test_strips_uppercase_banner(self):
        out = strip_fallback_banner(self.UPPERCASE)
        assert "Non-LLM Extractive Summary" not in out
        assert "actual signal body" in self.UPPERCASE  # sanity
        assert out.endswith("The actual signal body follows here.")

    def test_strips_lowercase_banner(self):
        out = strip_fallback_banner(self.LOWERCASE)
        assert "non-llm extractive summary" not in out
        assert out.endswith("Body text after the banner.")

    def test_idempotent(self):
        once = strip_fallback_banner(self.UPPERCASE)
        twice = strip_fallback_banner(once)
        assert once == twice

    def test_no_banner_passthrough(self):
        body = "Plain summary without any banner."
        assert strip_fallback_banner(body) == body

    def test_empty_string(self):
        assert strip_fallback_banner("") == ""

    def test_none_safe(self):
        # Function should not blow up on None.
        assert strip_fallback_banner(None) is None
