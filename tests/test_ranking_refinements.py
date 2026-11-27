"""Phase 4.6 — prioritisation refinement tests.

Covers the four ranking signals in _rank_articles_by_relevance: recency
weighting + tie-breaks, source diversity, near-duplicate demotion, and
classification-gate confidence — plus the all-off legacy path and composition
with the Phase 4.3 aging boost.

Scoring note: _score_article_relevance adds each theme keyword's weight once
(substring match), so with the fixture keywords {agent: 3, framework: 2,
benchmarks: 1} a title's score is the sum of the distinct keywords it contains.
"""
from datetime import datetime, timedelta, timezone

import pytest

import core.processed_articles as processed_articles
import core.summariser as summariser


@pytest.fixture
def rank_env(monkeypatch):
    """Isolated ranking environment: no times-seen counters, a known theme,
    and a settings overlay controlled per-test via `set_knobs`."""
    monkeypatch.setattr(processed_articles, "get_times_seen", lambda theme: {})
    monkeypatch.setitem(
        summariser.THEMES, "RankTheme",
        {"keywords": {"agent": 3, "framework": 2, "benchmarks": 1}},
    )
    state = {}

    def set_knobs(**overrides):
        state.update(overrides)
        monkeypatch.setattr(
            "config.settings.load_custom_settings", lambda: dict(state)
        )

    return set_knobs


def _art(title, hash_, source="SrcA", days_old=None, gate=None):
    a = {"title": title, "content_hash": hash_, "source_name": source}
    if days_old is not None:
        dt = datetime.now(timezone.utc) - timedelta(days=days_old)
        a["published_date"] = dt.isoformat()
    if gate is not None:
        a["gate"] = gate
    return a


# ---------------------------------------------------------------------------
# Recency weighting + tie-breaks
# ---------------------------------------------------------------------------

def test_recency_prefers_newer_on_relevance_tie(rank_env):
    rank_env()
    old = _art("agent roadmap", "old", days_old=12)
    new = _art("agent roadmap", "new", days_old=1)
    ranked = summariser._rank_articles_by_relevance([old, new], "RankTheme", 2)
    assert [a["content_hash"] for a in ranked] == ["new", "old"]


def test_recency_zero_disables_bonus_and_tiebreak(rank_env):
    rank_env(recency_weight=0.0)
    old = _art("agent roadmap", "old", days_old=12)
    new = _art("agent roadmap", "new", days_old=1)
    ranked = summariser._rank_articles_by_relevance([old, new], "RankTheme", 2)
    # Equal scores, recency signal fully off -> insertion order
    assert [a["content_hash"] for a in ranked] == ["old", "new"]


def test_recency_bonus_decays_with_age(rank_env):
    rank_env()
    stale = _art("agent roadmap", "stale", days_old=30)   # 3 + 0 (past lookback)
    mid = _art("agent roadmap", "mid", days_old=7)        # 3 + ~0.5
    fresh = _art("agent roadmap", "fresh", days_old=0)    # 3 + ~1.0
    ranked = summariser._rank_articles_by_relevance([stale, mid, fresh], "RankTheme", 3)
    assert [a["content_hash"] for a in ranked] == ["fresh", "mid", "stale"]


def test_tie_break_stable_without_dates(rank_env):
    rank_env()
    arts = [_art("agent", "b"), _art("agent", "a")]
    ranked = summariser._rank_articles_by_relevance(arts, "RankTheme", 2)
    assert [a["content_hash"] for a in ranked] == ["b", "a"]


def test_unparseable_date_is_neutral(rank_env):
    rank_env()
    bad = _art("agent roadmap", "bad")
    bad["published_date"] = "not-a-date"
    good = _art("agent roadmap", "good", days_old=1)
    ranked = summariser._rank_articles_by_relevance([bad, good], "RankTheme", 2)
    assert ranked[0]["content_hash"] == "good"


# ---------------------------------------------------------------------------
# Source diversity
# ---------------------------------------------------------------------------

def test_source_diversity_demotes_repeat_source(rank_env):
    rank_env()
    # Distinct titles, equal scores (Jaccard 0.5 < 0.8, so no dup demotion)
    a1 = _art("agent framework rollout", "a1", source="SrcA")   # 5
    a2 = _art("agent framework pricing", "a2", source="SrcA")   # 5
    b1 = _art("agent", "b1", source="SrcB")                     # 3
    # Default penalty 1.5: a2 effective 3.5 still beats b1 -> score order kept
    ranked = summariser._rank_articles_by_relevance([a1, a2, b1], "RankTheme", 2)
    assert [a["content_hash"] for a in ranked] == ["a1", "a2"]
    # Penalty 4.0: a2 effective 1.0 loses to b1 -> diversity wins
    rank_env(source_diversity_penalty=4.0)
    ranked = summariser._rank_articles_by_relevance([a1, a2, b1], "RankTheme", 2)
    assert [a["content_hash"] for a in ranked] == ["a1", "b1"]


def test_source_diversity_zero_keeps_score_order(rank_env):
    rank_env(source_diversity_penalty=0.0)
    a1 = _art("agent framework rollout", "a1", source="SrcA")
    a2 = _art("agent framework pricing", "a2", source="SrcA")
    b1 = _art("agent", "b1", source="SrcB")
    ranked = summariser._rank_articles_by_relevance([a1, a2, b1], "RankTheme", 2)
    assert [a["content_hash"] for a in ranked] == ["a1", "a2"]


def test_diversity_never_drops_articles_when_pool_small(rank_env):
    rank_env(source_diversity_penalty=100.0)
    arts = [_art("agent", f"h{i}", source="SameSrc") for i in range(3)]
    ranked = summariser._rank_articles_by_relevance(arts, "RankTheme", 5)
    assert len(ranked) == 3


# ---------------------------------------------------------------------------
# Near-duplicate demotion
# ---------------------------------------------------------------------------

def test_syndicated_copy_demoted_below_distinct_article(rank_env):
    rank_env()
    original = _art("openai launches agent framework for enterprise", "orig", source="SrcA")  # 5
    copy = _art("openai launches agent framework for enterprise", "copy", source="SrcB")      # 5 - 4
    distinct = _art("agent benchmarks", "distinct", source="SrcC")                            # 4
    ranked = summariser._rank_articles_by_relevance(
        [original, copy, distinct], "RankTheme", 2
    )
    assert [a["content_hash"] for a in ranked] == ["orig", "distinct"]


def test_near_duplicate_threshold_respected(rank_env):
    rank_env()
    # Titles share 2 of 6 distinct tokens -> Jaccard 0.33 < 0.8: no demotion
    a = _art("agent framework launches today", "a", source="SrcA")
    b = _art("agent framework pricing announced", "b", source="SrcB")
    ranked = summariser._rank_articles_by_relevance([a, b], "RankTheme", 2)
    assert [x["content_hash"] for x in ranked] == ["a", "b"]


def test_duplicates_still_fed_when_top_n_covers_pool(rank_env):
    rank_env()
    original = _art("openai launches agent framework", "orig", source="SrcA")
    copy = _art("openai launches agent framework", "copy", source="SrcB")
    ranked = summariser._rank_articles_by_relevance([original, copy], "RankTheme", 5)
    assert {a["content_hash"] for a in ranked} == {"orig", "copy"}
    assert ranked[0]["content_hash"] == "orig"


def test_duplicate_penalty_zero_disables_clustering(rank_env):
    rank_env(near_duplicate_penalty=0.0)
    original = _art("openai launches agent framework", "orig", source="SrcA")
    copy = _art("openai launches agent framework", "copy", source="SrcA")
    distinct = _art("agent", "distinct", source="SrcC")
    ranked = summariser._rank_articles_by_relevance(
        [original, copy, distinct], "RankTheme", 2
    )
    assert [a["content_hash"] for a in ranked] == ["orig", "copy"]


# ---------------------------------------------------------------------------
# Classification-gate confidence
# ---------------------------------------------------------------------------

def test_gate_confidence_prefers_keyword_gate(rank_env):
    rank_env()
    heuristic = _art("agent roadmap", "h4", gate=4)
    keyword = _art("agent roadmap", "h1", gate=1)
    ranked = summariser._rank_articles_by_relevance([heuristic, keyword], "RankTheme", 2)
    assert [a["content_hash"] for a in ranked] == ["h1", "h4"]


def test_gate_weight_zero_disables_signal(rank_env):
    rank_env(gate_confidence_weight=0.0)
    heuristic = _art("agent roadmap", "h4", gate=4)
    keyword = _art("agent roadmap", "h1", gate=1)
    ranked = summariser._rank_articles_by_relevance([heuristic, keyword], "RankTheme", 2)
    assert [a["content_hash"] for a in ranked] == ["h4", "h1"]


def test_missing_gate_is_neutral_not_penalised(rank_env):
    rank_env()
    no_gate = _art("agent roadmap", "none")
    gate4 = _art("agent roadmap", "h4", gate=4)
    ranked = summariser._rank_articles_by_relevance([gate4, no_gate], "RankTheme", 2)
    assert ranked[0]["content_hash"] == "none"


# ---------------------------------------------------------------------------
# Legacy path, composition, defaults
# ---------------------------------------------------------------------------

def test_all_signals_off_matches_legacy_order(rank_env):
    rank_env(
        aging_boost_per_run=0.0, recency_weight=0.0,
        source_diversity_penalty=0.0, near_duplicate_penalty=0.0,
        gate_confidence_weight=0.0,
    )
    arts = [
        _art("agent", "low", days_old=0, gate=1),                    # 3
        _art("agent framework benchmarks", "high", days_old=13, gate=4),  # 6
        _art("agent framework", "mid"),                              # 5
    ]
    ranked = summariser._rank_articles_by_relevance(arts, "RankTheme", 3)
    assert [a["content_hash"] for a in ranked] == ["high", "mid", "low"]


def test_all_off_no_keywords_keeps_insertion_order(rank_env):
    rank_env(
        aging_boost_per_run=0.0, recency_weight=0.0,
        source_diversity_penalty=0.0, near_duplicate_penalty=0.0,
        gate_confidence_weight=0.0,
    )
    summariser.THEMES.pop("RankEmptyTheme", None)
    arts = [{"title": "b", "content_hash": "b"}, {"title": "a", "content_hash": "a"}]
    ranked = summariser._rank_articles_by_relevance(arts, "RankEmptyTheme", 2)
    assert [a["content_hash"] for a in ranked] == ["b", "a"]


def test_aging_boost_still_composes(rank_env, monkeypatch):
    rank_env()
    fresh = _art("agent framework", "fresh", days_old=0, gate=1)   # 5 + ~1.0 + 0.5 = ~6.5
    waiting = _art("unrelated words", "waiting", days_old=13, gate=4)
    monkeypatch.setattr(
        processed_articles, "get_times_seen", lambda theme: {"waiting": 2}
    )
    ranked = summariser._rank_articles_by_relevance([fresh, waiting], "RankTheme", 1)
    # waiting ~ 2.0*2 + 0.07 = ~4.1 < 6.5 -> fresh still wins
    assert ranked[0]["content_hash"] == "fresh"
    monkeypatch.setattr(
        processed_articles, "get_times_seen", lambda theme: {"waiting": 4}
    )
    ranked = summariser._rank_articles_by_relevance([fresh, waiting], "RankTheme", 1)
    # waiting ~ 8.1 > 6.5 -> the 4.3 drain contract still flips the order
    assert ranked[0]["content_hash"] == "waiting"


def test_top_n_zero_returns_empty(rank_env):
    rank_env()
    assert summariser._rank_articles_by_relevance([_art("agent", "a")], "RankTheme", 0) == []


def test_settings_defaults_present():
    from config.settings import get_summariser_settings
    s = get_summariser_settings()
    assert s.get("recency_weight") == 1.0
    assert s.get("source_diversity_penalty") == 1.5
    assert s.get("near_duplicate_penalty") == 4.0
    assert s.get("near_duplicate_threshold") == 0.8
    assert s.get("gate_confidence_weight") == 0.5
