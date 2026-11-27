"""Phase 4.3 — fair drain / backlog carryover tests.

Covers: the aging boost in _rank_articles_by_relevance, the times-seen
counter (local fallback + Supabase RPC path), and the aged-out record
computed at persistence time.
"""
import asyncio
import json
from unittest.mock import MagicMock

import pytest

import core.history_manager as history_manager
import core.processed_articles as processed_articles
import core.summariser as summariser
from core.supabase_client import SupabaseManager


@pytest.fixture
def local_ledgers(tmp_path, monkeypatch):
    """Point both local ledger files at tmp_path (Supabase is offline via conftest)."""
    monkeypatch.setattr(processed_articles, "LOCAL_PROCESSED_FILE", tmp_path / "processed.json")
    monkeypatch.setattr(processed_articles, "LOCAL_TIMES_SEEN_FILE", tmp_path / "times_seen.json")
    return tmp_path


# ---------------------------------------------------------------------------
# Aging boost in ranking
# ---------------------------------------------------------------------------

def test_aging_boost_promotes_long_waiting_article(local_ledgers, monkeypatch):
    monkeypatch.setitem(summariser.THEMES, "FairDrainTheme", {"keywords": {"agent": 3}})
    fresh = {"title": "agent agent agent", "content_hash": "fresh"}
    waiting = {"title": "unrelated words", "content_hash": "waiting"}
    monkeypatch.setattr(processed_articles, "get_times_seen", lambda theme: {"waiting": 5})

    ranked = summariser._rank_articles_by_relevance([fresh, waiting], "FairDrainTheme", 2)
    # fresh scores 9, waiting scores 0 + 2.0*5 = 10 -> waiting first
    assert ranked[0]["content_hash"] == "waiting"


def test_zero_boost_disables_aging(local_ledgers, monkeypatch):
    monkeypatch.setitem(summariser.THEMES, "FairDrainTheme", {"keywords": {"agent": 3}})
    monkeypatch.setattr(
        "config.settings.load_custom_settings", lambda: {"aging_boost_per_run": 0.0}
    )
    fresh = {"title": "agent agent agent", "content_hash": "fresh"}
    waiting = {"title": "unrelated words", "content_hash": "waiting"}
    monkeypatch.setattr(processed_articles, "get_times_seen", lambda theme: {"waiting": 50})

    ranked = summariser._rank_articles_by_relevance([fresh, waiting], "FairDrainTheme", 2)
    assert ranked[0]["content_hash"] == "fresh"


def test_no_keywords_no_seen_keeps_insertion_order(local_ledgers):
    monkeypatch_theme = "FairDrainEmptyTheme"
    summariser.THEMES.pop(monkeypatch_theme, None)
    arts = [{"title": "b", "content_hash": "b"}, {"title": "a", "content_hash": "a"}]
    ranked = summariser._rank_articles_by_relevance(arts, monkeypatch_theme, 2)
    assert [a["content_hash"] for a in ranked] == ["b", "a"]


def test_settings_default_boost_present():
    from config.settings import get_summariser_settings
    assert get_summariser_settings().get("aging_boost_per_run") == 2.0


# ---------------------------------------------------------------------------
# Times-seen counter: local fallback
# ---------------------------------------------------------------------------

def test_record_run_seen_local_increment_and_reset(local_ledgers):
    processed_articles.record_run_seen("T", ["h1", "h2"], ["h3"])
    assert processed_articles.get_times_seen("T") == {"h1": 1, "h2": 1}

    processed_articles.record_run_seen("T", ["h1"], ["h2"])
    assert processed_articles.get_times_seen("T") == {"h1": 2}

    # included resets even a long-waiting hash; empty call is a no-op
    processed_articles.record_run_seen("T", [], ["h1"])
    assert processed_articles.get_times_seen("T") == {}
    processed_articles.record_run_seen("T", [], [])
    assert processed_articles.get_times_seen("T") == {}


def test_record_run_seen_prefers_supabase(local_ledgers, monkeypatch):
    manager = MagicMock()
    manager.is_available.return_value = True
    monkeypatch.setattr("core.supabase_client.get_supabase_manager", lambda: manager)

    processed_articles.record_run_seen("T", ["h1"], ["h2"])
    manager.record_run_seen.assert_called_once_with("T", ["h1"], ["h2"])
    # local file must NOT be written when the cloud path succeeded
    assert not processed_articles.LOCAL_TIMES_SEEN_FILE.exists()


def test_get_times_seen_prefers_supabase(local_ledgers, monkeypatch):
    manager = MagicMock()
    manager.is_available.return_value = True
    manager.get_times_seen.return_value = {"h1": 3}
    monkeypatch.setattr("core.supabase_client.get_supabase_manager", lambda: manager)
    assert processed_articles.get_times_seen("T") == {"h1": 3}


# ---------------------------------------------------------------------------
# Supabase manager methods
# ---------------------------------------------------------------------------

def _manager_with_client():
    m = SupabaseManager.__new__(SupabaseManager)
    m.available = True
    m.client = MagicMock()
    return m


def test_manager_record_run_seen_calls_rpc():
    m = _manager_with_client()
    m.record_run_seen("T", ["h1"], ["h2"])
    m.client.rpc.assert_called_once_with("record_article_seen", {
        "p_theme": "T", "p_waiting": ["h1"], "p_included": ["h2"],
    })


def test_manager_record_run_seen_noop_when_empty():
    m = _manager_with_client()
    m.record_run_seen("T", [], [])
    m.client.rpc.assert_not_called()


def test_manager_get_times_seen_filters_positive():
    m = _manager_with_client()
    chain = m.client.table.return_value.select.return_value.eq.return_value.gt.return_value
    chain.execute.return_value.data = [
        {"content_hash": "h1", "times_seen": 4},
        {"content_hash": None, "times_seen": 2},
    ]
    assert m.get_times_seen("T") == {"h1": 4}


def test_manager_get_times_seen_degrades_on_error():
    m = _manager_with_client()
    chain = m.client.table.return_value.select.return_value.eq.return_value.gt.return_value
    chain.execute.side_effect = RuntimeError("column missing (pre-migration)")
    assert m.get_times_seen("T") == {}


# ---------------------------------------------------------------------------
# Aged-out record at persistence time
# ---------------------------------------------------------------------------

@pytest.fixture
def history_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(history_manager, "HISTORY_JSON", tmp_path / "history.json")
    monkeypatch.setattr(history_manager, "MEMORY_MD", tmp_path / "memory.md")
    monkeypatch.setattr(history_manager, "_history_cache", None)
    return tmp_path


def _prev_run(waiting, themed):
    return {
        "2026-11-26 08:00:00": {
            "date": "2026-11-26",
            "summaries": {"T": {"waiting_hashes": waiting, "_generation_log": {}}},
            "themed_articles": {"T": themed},
            "counts": {"T": len(themed)},
            "full_articles": themed,
        }
    }


def test_aged_out_recorded_for_departed_waiting(history_paths):
    art_a = {"title": "A gone", "content_hash": "hA", "source_name": "S", "link": "http://a"}
    art_b = {"title": "B still here", "content_hash": "hB", "source_name": "S", "link": "http://b"}
    history_paths.joinpath("history.json").write_text(
        json.dumps(_prev_run(["hA", "hB"], [art_a, art_b])), encoding="utf-8"
    )
    summaries = {"T": {"what_is_happening": "x", "_generation_log": {}}}
    history_manager.save_run_to_history(
        summaries, {"T": 1}, [art_b], {"T": [art_b]},
    )
    aged = summaries["T"]["aged_out_articles"]
    assert [a["hash"] for a in aged] == ["hA"]
    assert aged[0]["title"] == "A gone"
    assert aged[0]["last_seen"] == "2026-11-26 08:00:00"
    assert summaries["T"]["_generation_log"]["aged_out_articles"] == aged
    # persisted into the new history entry too
    stored = json.loads(history_paths.joinpath("history.json").read_text(encoding="utf-8"))
    newest = sorted(stored.keys(), reverse=True)[0]
    assert stored[newest]["summaries"]["T"]["aged_out_articles"] == aged


def test_still_waiting_not_aged_out(history_paths):
    art_b = {"title": "B", "content_hash": "hB"}
    history_paths.joinpath("history.json").write_text(
        json.dumps(_prev_run(["hB"], [art_b])), encoding="utf-8"
    )
    summaries = {"T": {"what_is_happening": "x"}}
    history_manager.save_run_to_history(summaries, {"T": 1}, [art_b], {"T": [art_b]})
    assert summaries["T"]["aged_out_articles"] == []


def test_no_previous_run_no_aged_out(history_paths):
    summaries = {"T": {"what_is_happening": "x"}}
    history_manager.save_run_to_history(summaries, {"T": 0}, [], {"T": []})
    assert summaries["T"]["aged_out_articles"] == []


def test_aged_out_reads_waiting_from_generation_log(history_paths):
    art_a = {"title": "A", "content_hash": "hA"}
    prev = _prev_run([], [art_a])
    prev["2026-11-26 08:00:00"]["summaries"]["T"] = {
        "_generation_log": {"waiting_hashes": ["hA"]},
    }
    history_paths.joinpath("history.json").write_text(json.dumps(prev), encoding="utf-8")
    summaries = {"T": {"what_is_happening": "x"}}
    history_manager.save_run_to_history(summaries, {"T": 0}, [], {"T": []})
    assert [a["hash"] for a in summaries["T"]["aged_out_articles"]] == ["hA"]


# ---------------------------------------------------------------------------
# Orchestrator: waiting hashes + counter advance on the gateway path
# ---------------------------------------------------------------------------

def test_orchestrator_records_waiting_and_advances_counter(local_ledgers, monkeypatch):
    theme = summariser.THEME_ORDER[0]
    arts = [
        {"title": f"art {i}", "content_hash": f"h{i}", "summary": "s"}
        for i in range(1, 4)
    ]

    async def fake_gateway(theme_name, articles):
        return {"what_is_happening": "x", "_generation_log": {}}, ["h1"]

    monkeypatch.setattr(summariser, "generate_theme_summary_gateway", fake_gateway)

    summaries, processed = asyncio.run(
        summariser._generate_all_summaries_async({theme: arts})
    )
    assert set(summaries[theme]["waiting_hashes"]) == {"h2", "h3"}
    assert summaries[theme]["_generation_log"]["waiting_hashes"] == summaries[theme]["waiting_hashes"]
    assert processed_articles._load_local_times_seen().get(theme) == {"h2": 1, "h3": 1}
    assert processed[theme] == ["h1"]