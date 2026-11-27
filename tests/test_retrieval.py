"""Phase 5 — hybrid retrieval tests (offline).

Covers: temporal bound parsing, RRF fusion math, hybrid_search leg
degradation / decay / bounds, Sage wiring (selection order, normalisation,
legacy fallback for non-real clients), theme_first_seen accessors, and the
eval runner's hybrid arm.
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.retrieval import hybrid_search, parse_temporal_bounds, rrf_fuse
from core.supabase_client import SupabaseManager

NOW = datetime(2026, 11, 28, 12, 0, 0, tzinfo=timezone.utc)


def _spec_sb(sparse=None, dense=None):
    sb = MagicMock(spec=SupabaseManager)
    sb.is_available.return_value = True
    sb.match_articles_fts.return_value = sparse
    sb.match_article_embeddings.return_value = dense
    return sb


def _no_embeddings():
    return patch("core.embedding.embed_texts", return_value=([], None))


def _embeddings_ok():
    return patch("core.embedding.embed_texts", return_value=([[0.0] * 768], "test-model"))


# ---------------------------------------------------------------------------
# Temporal parsing (5.3)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    ("what happened last week", ("2026-11-21", "2026-11-28")),
    ("developments in the past 3 days", ("2026-11-25", "2026-11-28")),
    ("last 2 weeks of agent news", ("2026-11-14", "2026-11-28")),
    ("what launched in August 2026", ("2026-08-01", "2026-08-31")),
    ("between June and September 2026", ("2026-06-01", "2026-09-30")),
    ("between November and February", ("2026-11-01", "2027-02-28")),
    ("since July", ("2026-07-01", "2026-11-28")),
    ("major releases in 2026", ("2026-01-01", "2026-12-31")),
    ("last month", ("2026-10-01", "2026-10-31")),
    ("this month", ("2026-11-01", "2026-11-28")),
    ("today", ("2026-11-28", "2026-11-28")),
    ("yesterday", ("2026-11-27", "2026-11-27")),
    ("what is retrieval augmented generation", (None, None)),
    ("", (None, None)),
])
def test_parse_temporal_bounds(query, expected):
    assert parse_temporal_bounds(query, now=NOW) == expected


# ---------------------------------------------------------------------------
# RRF fusion (5.2)
# ---------------------------------------------------------------------------

def test_rrf_both_legs_accumulate():
    sparse = [{"id": "x", "title": "X"}]
    dense = [{"article_id": "x", "chunk_text": "X chunk", "similarity": 0.9}]
    fused = rrf_fuse(sparse, dense, k=60)
    assert len(fused) == 1
    assert fused[0]["_rrf_score"] == pytest.approx(2.0 / 61)
    assert fused[0]["chunk_text"] == "X chunk"
    assert fused[0]["_fts_rank"] == 1 and fused[0]["_dense_rank"] == 1


def test_rrf_dual_leg_outranks_single_leg():
    sparse = [{"id": "both", "title": "B"}, {"id": "only-sparse", "title": "S"}]
    dense = [{"article_id": "both", "chunk_text": "B"}, {"article_id": "only-dense", "chunk_text": "D"}]
    fused = rrf_fuse(sparse, dense, k=60)
    assert fused[0]["title"] == "B" or fused[0].get("chunk_text") == "B"
    assert fused[0]["_rrf_score"] > fused[1]["_rrf_score"]


def test_rrf_sparse_only_gets_chunk_haystack():
    fused = rrf_fuse([{"id": "s", "title": "Title", "summary": "Body"}], [], k=60)
    assert fused[0]["chunk_text"] == "Title Body"


def test_rrf_empty_legs():
    assert rrf_fuse([], [], k=60) == []
    assert rrf_fuse(None, None, k=60) == []


# ---------------------------------------------------------------------------
# hybrid_search: legs, degradation, decay, bounds
# ---------------------------------------------------------------------------

def test_hybrid_both_legs_provenance():
    sb = _spec_sb(sparse=[{"id": "a", "title": "A"}],
                  dense=[{"article_id": "b", "chunk_text": "B", "similarity": 0.8}])
    with _embeddings_ok():
        out = hybrid_search("agent frameworks", supabase=sb,
                            settings={"retrieval_time_decay_floor": 1.0})
    assert len(out) == 2
    assert all(e["_retrieval"] == "hybrid" for e in out)


def test_hybrid_degrades_to_sparse_when_embeddings_fail():
    sb = _spec_sb(sparse=[{"id": "a", "title": "A"}], dense=[])
    with _no_embeddings():
        out = hybrid_search("agent frameworks", supabase=sb)
    assert len(out) == 1 and out[0]["_retrieval"] == "sparse"
    sb.match_article_embeddings.assert_not_called()


def test_hybrid_degrades_to_dense_when_fts_missing():
    sb = _spec_sb(sparse=None, dense=[{"article_id": "b", "chunk_text": "B", "similarity": 0.8}])
    with _embeddings_ok():
        out = hybrid_search("agent frameworks", supabase=sb)
    assert len(out) == 1 and out[0]["_retrieval"] == "dense"


def test_hybrid_both_empty_returns_empty():
    sb = _spec_sb(sparse=[], dense=[])
    with _embeddings_ok():
        assert hybrid_search("agent frameworks", supabase=sb) == []


def test_hybrid_unavailable_supabase():
    sb = MagicMock(spec=SupabaseManager)
    sb.is_available.return_value = False
    assert hybrid_search("q", supabase=sb) == []
    assert hybrid_search("", supabase=_spec_sb()) == []


def test_time_decay_prefers_fresh_at_equal_rrf():
    old = (NOW - timedelta(days=100)).isoformat()
    sparse = [{"id": "old", "title": "Old", "published_at": old}]
    dense = [{"article_id": "new", "chunk_text": "New", "similarity": 0.9,
              "published_at": NOW.isoformat()}]
    sb = _spec_sb(sparse=sparse, dense=dense)
    with _embeddings_ok(), patch("core.retrieval.datetime") as mock_dt:
        mock_dt.now.return_value = NOW
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.strptime = datetime.strptime
        out = hybrid_search("agent frameworks", supabase=sb)
    # Equal RRF (each rank 1 in its leg); decay lifts the fresh article.
    assert out[0].get("article_id") == "new" or out[0].get("title") == "New"


def test_explicit_bounds_disable_decay():
    old = (NOW - timedelta(days=100)).isoformat()
    sparse = [{"id": "old", "title": "Old", "published_at": old}]
    dense = [{"article_id": "new", "chunk_text": "New", "similarity": 0.9,
              "published_at": NOW.isoformat()}]
    sb = _spec_sb(sparse=sparse, dense=dense)
    with _embeddings_ok():
        out = hybrid_search("agent frameworks", supabase=sb, date_from="2026-01-01")
    # No decay: equal RRF scores keep insertion (sparse-first) order.
    assert out[0]["title"] == "Old"


def test_parsed_bounds_reach_both_rpcs():
    sb = _spec_sb(sparse=[], dense=[])
    with _embeddings_ok():
        hybrid_search("what launched in August 2026", supabase=sb)
    fts_kwargs = sb.match_articles_fts.call_args.kwargs
    ann_kwargs = sb.match_article_embeddings.call_args.kwargs
    assert fts_kwargs["date_from"] == "2026-08-01"
    assert fts_kwargs["date_to"] == "2026-08-31"
    assert ann_kwargs["date_from"] == "2026-08-01"
    assert ann_kwargs["date_to"] == "2026-08-31"


def test_caller_bounds_intersect_parsed():
    sb = _spec_sb(sparse=[], dense=[])
    with _embeddings_ok():
        hybrid_search("what launched in August 2026", supabase=sb,
                      date_from="2026-08-15", date_to="2026-12-31")
    kwargs = sb.match_articles_fts.call_args.kwargs
    assert kwargs["date_from"] == "2026-08-15"  # later lower bound wins
    assert kwargs["date_to"] == "2026-08-31"    # earlier upper bound wins


def test_source_filter_applied_post_fusion():
    sb = _spec_sb(sparse=[{"id": "a", "title": "A", "source_name": "Keep"}],
                  dense=[{"article_id": "b", "chunk_text": "B", "similarity": 0.9,
                          "source_name": "Drop"}])
    with _embeddings_ok():
        out = hybrid_search("q", supabase=sb, source_filter="Keep")
    assert len(out) == 1 and out[0]["source_name"] == "Keep"


def test_top_k_caps_results():
    sb = _spec_sb(sparse=[{"id": f"s{i}", "title": f"S{i}"} for i in range(10)], dense=[])
    with _no_embeddings():
        out = hybrid_search("q", supabase=sb, top_k=3)
    assert len(out) == 3


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def test_retrieval_settings_defaults():
    from config.settings import get_retrieval_settings
    s = get_retrieval_settings()
    assert s["hybrid_retrieval_enabled"] is True
    assert s["rrf_k"] == 60
    assert s["retrieval_dense_candidates"] == 50
    assert s["retrieval_sparse_candidates"] == 50
    assert s["retrieval_time_decay_floor"] == 0.9
    assert s["retrieval_time_decay_halflife_days"] == 180.0


# ---------------------------------------------------------------------------
# Sage wiring (5.1)
# ---------------------------------------------------------------------------

def test_normalise_article_carries_retrieval_keys():
    from core.sage_agent import _normalise_article
    row = {"title": "T", "summary": "S", "theme_name": "Th", "source_name": "Src",
           "link": "https://x", "run_date": "2026-11-01",
           "_rrf_score": 0.03, "_retrieval": "hybrid", "chunk_text": "T S"}
    out = _normalise_article(row)
    assert out["_date"] == "2026-11-01"
    assert out["_rrf_score"] == 0.03
    assert out["_retrieval"] == "hybrid"
    assert out["chunk_text"] == "T S"


def test_select_articles_preserves_hybrid_order():
    from core.sage_agent import _select_articles
    # Keyword scoring would rank "agent agent" first; hybrid order must win.
    candidates = [
        {"title": "unrelated", "summary": "", "_date": "2026-11-01", "_rrf_score": 0.03},
        {"title": "agent agent", "summary": "", "_date": "2026-11-02", "_rrf_score": 0.02},
    ]
    out = _select_articles(candidates, ["agent"])
    assert out[0]["title"] == "unrelated"


def test_select_articles_legacy_path_unchanged():
    from core.sage_agent import _select_articles
    candidates = [
        {"title": "unrelated", "summary": "", "_date": "2026-11-01"},
        {"title": "agent agent", "summary": "", "_date": "2026-11-02"},
    ]
    out = _select_articles(candidates, ["agent"])
    assert out[0]["title"] == "agent agent"


def test_fetch_candidates_uses_hybrid_for_real_client():
    import core.sage_agent as sage
    sb = MagicMock(spec=SupabaseManager)
    sb.is_available.return_value = True
    fused = [{"title": "Fused", "summary": "s", "theme_name": "Th",
              "source_name": "Src", "link": "https://x", "published_at": "2026-11-20",
              "_rrf_score": 0.03, "_retrieval": "hybrid", "chunk_text": "Fused s"}]
    with patch("core.retrieval.hybrid_search", return_value=fused) as mock_hybrid:
        out = sage._fetch_article_candidates(sb, None, None, None, None, None, "question?")
    mock_hybrid.assert_called_once()
    assert out[0]["title"] == "Fused" and out[0]["_rrf_score"] == 0.03
    sb.get_articles_for_context.assert_not_called()


def test_fetch_candidates_bare_mock_keeps_legacy_path():
    import core.sage_agent as sage
    sb = MagicMock()  # no spec: stands in for pre-Phase-5 / stub clients
    sb.is_available.return_value = True
    sb.get_articles_for_context.return_value = [
        {"title": "Legacy", "summary": "", "theme_name": "Th", "source_name": "S",
         "link": "https://x", "published_at": "2026-11-20"}
    ]
    out = sage._fetch_article_candidates(sb, None, None, None, None, None, "question?")
    assert out[0]["title"] == "Legacy"
    sb.get_articles_for_context.assert_called_once()


def test_fetch_candidates_hybrid_empty_falls_through():
    import core.sage_agent as sage
    sb = MagicMock(spec=SupabaseManager)
    sb.is_available.return_value = True
    sb.get_articles_for_context.return_value = [
        {"title": "Window", "summary": "", "theme_name": "Th", "source_name": "S",
         "link": "https://x", "published_at": "2026-11-20"}
    ]
    with patch("core.retrieval.hybrid_search", return_value=[]):
        out = sage._fetch_article_candidates(sb, None, None, None, None, None, "question?")
    assert out[0]["title"] == "Window"


# ---------------------------------------------------------------------------
# theme_first_seen accessors (5.4)
# ---------------------------------------------------------------------------

def test_get_theme_first_seen_shapes_dict():
    from core.supabase_client import SupabaseManager
    sb = SupabaseManager.__new__(SupabaseManager)
    sb.available = True
    sb.client = MagicMock()
    sb.client.table.return_value.select.return_value.execute.return_value.data = [
        {"theme_name": "T1", "first_seen_date": "2026-05-01"},
        {"theme_name": "T2", "first_seen_date": "2026-05-02"},
    ]
    out = sb.get_theme_first_seen()
    assert out["T1"]["first_seen_date"] == "2026-05-01"
    assert len(out) == 2


def test_record_theme_first_seen_insert_if_absent():
    from core.supabase_client import SupabaseManager
    sb = SupabaseManager.__new__(SupabaseManager)
    sb.available = True
    sb.client = MagicMock()
    ok = sb.record_theme_first_seen("T1", "2026-05-01", "run-1", "evidence")
    assert ok is True
    upsert = sb.client.table.return_value.upsert
    assert upsert.call_args.kwargs["ignore_duplicates"] is True
    assert upsert.call_args.kwargs["on_conflict"] == "theme_name"


def test_first_seen_unavailable_degrades():
    from core.supabase_client import SupabaseManager
    sb = SupabaseManager.__new__(SupabaseManager)
    sb.available = False
    assert sb.get_theme_first_seen() is None
    assert sb.record_theme_first_seen("T", "2026-05-01") is False


# ---------------------------------------------------------------------------
# Eval runner hybrid arm
# ---------------------------------------------------------------------------

def test_eval_hybrid_arm_scores_and_latency(tmp_path):
    from core.retrieval_eval import run_retrieval_eval
    qfile = tmp_path / "questions.json"
    qfile.write_text(json.dumps({"questions": [
        {"id": "q1", "question": "what is X?", "expected_keywords": ["x"], "k": 10},
    ]}), encoding="utf-8")
    manager = MagicMock()
    manager.is_available.return_value = True
    with patch("core.supabase_client.get_supabase_manager", return_value=manager), \
         patch("core.retrieval.hybrid_search",
               return_value=[{"chunk_text": "X found", "_rrf_score": 0.03}]) as mock_h:
        summary = run_retrieval_eval(path=qfile, quiet=True, arm="hybrid")
    mock_h.assert_called_once()
    assert summary["arm"] == "hybrid"
    assert summary["mean_recall"] == 1.0
    assert summary["per_question"][0]["latency_ms"] >= 0
    assert summary["latency_p95_ms"] >= 0
    manager.upsert_app_setting.assert_called_once()
    snapshot = manager.upsert_app_setting.call_args.args[1]
    assert snapshot["arm"] == "hybrid"


def test_percentile():
    from core.retrieval_eval import _percentile
    assert _percentile([], 95) == 0.0
    assert _percentile([100.0], 95) == 100.0
    assert _percentile([1, 2, 3, 4, 5], 95) == 5
