"""Phase 4.4 — budget-aware capacity tests: configurable ceiling replaces the
flat 12, char budget still binds, per-article truncation is a settings knob."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from core.ai_gateway import AITaskResult
from core.summariser import generate_theme_summary_gateway, split_articles_for_prompt


def _fake_gateway():
    fake_result = MagicMock(spec=AITaskResult)
    fake_result.is_success.return_value = True
    fake_result.result = (
        "## 1. WHAT IS HAPPENING\nSignal.\n## 2. ENGINEERING TRADEOFFS\nTradeoffs.\n"
        "## 3. PRODUCT IMPACT\nImpact.\n## 4. ACTIONABLE WATCHLIST\n- Item\n"
        "## 5. STRATEGIC FURTHER READING\n- Art | Src | url"
    )
    prov = MagicMock()
    prov.method = "llm"
    prov.provider = "qwen"
    prov.model = "qwen3.8-flash"
    prov.task = "summarise"
    prov.latency_ms = 100
    prov.attempts = 1
    prov.fallback_used = False
    prov.to_dict.return_value = {}
    fake_result.provenance = prov
    gw = MagicMock()
    gw.execute = AsyncMock(return_value=fake_result)
    return gw


def _articles(n):
    return [
        {"title": f"Article {i}", "summary": f"Summary {i}",
         "link": f"https://example.com/{i}", "source_name": "Test"}
        for i in range(n)
    ]


def test_settings_defaults():
    from config.settings import get_summariser_settings
    s = get_summariser_settings()
    assert s["max_articles_per_summary"] == 50
    assert s["article_summary_chars"] == 300


def test_ceiling_50_replaces_flat_12():
    gw = _fake_gateway()
    with patch("core.summariser.get_gateway", return_value=gw):
        summary, included = asyncio.run(
            generate_theme_summary_gateway("Frontier Models & Benchmarks", _articles(60))
        )
    assert summary["included_count"] == 50
    assert summary["skipped_count"] == 10
    assert len(included) == 50


def test_custom_ceiling_override():
    gw = _fake_gateway()
    with patch("core.summariser.get_gateway", return_value=gw), \
            patch("config.settings.load_custom_settings",
                  return_value={"max_articles_per_summary": 20}):
        summary, _ = asyncio.run(
            generate_theme_summary_gateway("Frontier Models & Benchmarks", _articles(60))
        )
    assert summary["included_count"] == 20


def test_char_budget_still_binds_below_ceiling():
    gw = _fake_gateway()
    # Tiny context window -> char budget (ctx * 3 * 0.6) cuts well below 50.
    with patch("core.summariser.get_gateway", return_value=gw), \
            patch("config.settings.QWEN_NUM_CTX", 2000):
        summary, included = asyncio.run(
            generate_theme_summary_gateway("Frontier Models & Benchmarks", _articles(60))
        )
    assert summary["included_count"] < 50
    assert summary["included_count"] == len(included)
    assert summary["skipped_count"] == 60 - len(included)


def test_truncation_knob_controls_prompt_block():
    long_summary = "X" * 900
    arts = [{"title": "T", "summary": long_summary, "source_name": "S", "link": ""}]
    with patch("config.settings.load_custom_settings",
               return_value={"article_summary_chars": 50}):
        text, included = split_articles_for_prompt(arts)
    assert "X" * 50 in text
    assert "X" * 51 not in text
    assert len(included) == 1


def test_truncation_default_300():
    arts = [{"title": "T", "summary": "Y" * 900, "source_name": "S", "link": ""}]
    text, _ = split_articles_for_prompt(arts)
    assert "Y" * 300 in text
    assert "Y" * 301 not in text