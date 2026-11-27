"""Phase 4.5 — chunked overflow (map-reduce) summarisation tests."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import core.summariser as summariser
import core.processed_articles as processed_articles


def _arts(n, prefix="a"):
    return [
        {"title": f"{prefix}{i}", "summary": "s", "content_hash": f"{prefix}{i}",
         "source_name": "S", "link": f"https://x/{i}"}
        for i in range(n)
    ]


def test_settings_defaults():
    from config.settings import get_summariser_settings
    s = get_summariser_settings()
    assert s["mapreduce_batch_size"] == 12
    assert s["mapreduce_max_batches"] == 8


def test_batches_and_union_ledger(monkeypatch):
    calls = []

    async def fake_batch(theme, batch):
        calls.append(len(batch))
        return (
            {"what_is_happening": f"brief of {len(batch)}", "_generation_log": {}},
            [processed_articles.article_hash(a) for a in batch],
        )

    async def fake_reduce(theme, intermediates, ctx, n):
        return {"what_is_happening": "merged", "_generation_log": {}}, "qwen:test"

    monkeypatch.setattr(summariser, "generate_theme_summary_gateway", fake_batch)
    monkeypatch.setattr(summariser, "_reduce_intermediates", fake_reduce)
    summary, included = asyncio.run(
        summariser.generate_theme_summary_mapreduce("T", _arts(30))
    )
    assert calls == [12, 12, 6]
    assert len(included) == 30 and len(set(included)) == 30
    assert summary["included_count"] == 30
    assert summary["total_articles"] == 30
    assert summary["_generation_log"]["mapreduce"]["batches"] == 3


def test_max_batches_bounds_cost(monkeypatch):
    calls = []

    async def fake_batch(theme, batch):
        calls.append(len(batch))
        return {"what_is_happening": "b", "_generation_log": {}}, []

    async def fake_reduce(theme, intermediates, ctx, n):
        return {"what_is_happening": "m", "_generation_log": {}}, "qwen:test"

    monkeypatch.setattr(summariser, "generate_theme_summary_gateway", fake_batch)
    monkeypatch.setattr(summariser, "_reduce_intermediates", fake_reduce)
    with patch("config.settings.load_custom_settings",
               return_value={"mapreduce_batch_size": 12, "mapreduce_max_batches": 2}):
        _, included = asyncio.run(
            summariser.generate_theme_summary_mapreduce("T", _arts(100))
        )
    assert calls == [12, 12]  # 24 of 100 fed; the rest waits via 4.3
    assert included == []


def test_reduce_failure_merges_deterministically(monkeypatch):
    async def fake_batch(theme, batch):
        name = batch[0]["title"]
        return (
            {"what_is_happening": f"Development {name} happened.",
             "further_reading": f"- **{name}** | S | https://x/{name}",
             "_generation_log": {}},
            [processed_articles.article_hash(a) for a in batch],
        )

    monkeypatch.setattr(summariser, "generate_theme_summary_gateway", fake_batch)
    boom = MagicMock()
    boom.execute = AsyncMock(side_effect=RuntimeError("reduce down"))
    monkeypatch.setattr(summariser, "get_gateway", lambda: boom)
    summary, included = asyncio.run(
        summariser.generate_theme_summary_mapreduce("T", _arts(24))
    )
    assert summary["_source"] == "extractive:mapreduce_merge"
    assert "Development a0 happened." in summary["what_is_happening"]
    assert "Development a12 happened." in summary["what_is_happening"]
    assert len(included) == 24
    # shared titles dedupe in merged further reading
    assert summary["further_reading"].count("**a0**") == 1


def test_reduce_output_budget_scales_with_batches(monkeypatch):
    async def fake_batch(theme, batch):
        return {"what_is_happening": "b", "_generation_log": {}}, []

    captured = {}
    res = MagicMock()
    res.is_success.return_value = True
    res.provenance.method = "llm"
    res.provenance.provider = "qwen"
    res.provenance.model = "m"
    res.result = (
        "## 1. WHAT IS HAPPENING\nx\n## 2. ENGINEERING TRADEOFFS\nx\n"
        "## 3. PRODUCT IMPACT\nx\n## 4. ACTIONABLE WATCHLIST\n- i\n"
        "## 5. STRATEGIC FURTHER READING\n- t | s | u"
    )
    gw = MagicMock()

    async def execute(req):
        captured["req"] = req
        return res

    gw.execute = execute
    monkeypatch.setattr(summariser, "generate_theme_summary_gateway", fake_batch)
    monkeypatch.setattr(summariser, "get_gateway", lambda: gw)
    with patch("config.settings.load_custom_settings",
               return_value={"mapreduce_batch_size": 3, "mapreduce_max_batches": 8}):
        asyncio.run(summariser.generate_theme_summary_mapreduce("T", _arts(24)))
    # 8 batches -> at least 1600 + 220*8 = 3360 output tokens
    assert captured["req"].max_tokens >= 3360


def test_orchestrator_triggers_mapreduce_only_on_overflow(monkeypatch, tmp_path):
    monkeypatch.setattr(processed_articles, "LOCAL_PROCESSED_FILE", tmp_path / "p.json")
    monkeypatch.setattr(processed_articles, "LOCAL_TIMES_SEEN_FILE", tmp_path / "t.json")
    theme = summariser.THEME_ORDER[0]
    mr_calls, gw_calls = [], []

    async def fake_mr(theme_name, articles):
        mr_calls.append(len(articles))
        return {"what_is_happening": "mr", "_generation_log": {}}, ["h0"]

    async def fake_gw(theme_name, articles):
        gw_calls.append(len(articles))
        return {"what_is_happening": "gw", "_generation_log": {}}, []

    monkeypatch.setattr(summariser, "generate_theme_summary_mapreduce", fake_mr)
    monkeypatch.setattr(summariser, "generate_theme_summary_gateway", fake_gw)

    asyncio.run(summariser._generate_all_summaries_async({theme: _arts(60)}))
    # empty themes still hit the gateway with 0 articles — ignore those
    assert mr_calls == [60] and [c for c in gw_calls if c] == []

    mr_calls.clear()
    gw_calls.clear()
    asyncio.run(summariser._generate_all_summaries_async({theme: _arts(20)}))
    assert mr_calls == [] and [c for c in gw_calls if c] == [20]