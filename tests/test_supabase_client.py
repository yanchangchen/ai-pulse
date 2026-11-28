"""
Tests for SupabaseManager methods in core.supabase_client.
"""

from unittest.mock import MagicMock, patch
from core.supabase_client import SupabaseManager


def test_get_runs_summary():
    """Test get_runs_summary delegates to get_all_runs."""
    manager = SupabaseManager()
    manager.available = True
    
    mock_runs = [
        {"id": "run-1", "run_timestamp": "2026-08-08 00:00:00", "run_date": "2026-08-08", "total_articles": 10},
        {"id": "run-2", "run_timestamp": "2026-08-07 00:00:00", "run_date": "2026-08-07", "total_articles": 15},
    ]
    
    with patch.object(manager, "get_all_runs", return_value=mock_runs) as mock_get_all:
        result = manager.get_runs_summary(limit=5)
        mock_get_all.assert_called_once_with(limit=5)
        assert result == mock_runs


def test_get_summaries_by_run():
    """Test get_summaries_by_run converts summary list to dict keyed by theme_name."""
    manager = SupabaseManager()
    manager.available = True
    
    mock_summaries = [
        {"theme_name": "AI Models & Architectures", "article_count": 5, "what_is_happening": "New LLMs released"},
        {"theme_name": "Autonomous Agents", "article_count": 3, "what_is_happening": "New agents deployed"},
    ]
    
    with patch.object(manager, "get_summaries_for_run", return_value=mock_summaries) as mock_get_sum:
        result = manager.get_summaries_by_run("run-123")
        mock_get_sum.assert_called_once_with("run-123")
        assert len(result) == 2
        assert result["AI Models & Architectures"]["article_count"] == 5
        assert result["Autonomous Agents"]["article_count"] == 3


def test_get_summaries_by_run_empty():
    """Test get_summaries_by_run returns empty dict when no summaries exist."""
    manager = SupabaseManager()
    manager.available = True

    with patch.object(manager, "get_summaries_for_run", return_value=None):
        result = manager.get_summaries_by_run("run-123")
        assert result == {}


def _query_chain(rows):
    """A self-returning postgrest query-builder mock ending in ``rows``."""
    q = MagicMock()
    q.select.return_value = q
    q.eq.return_value = q
    q.gte.return_value = q
    q.lte.return_value = q
    q.order.return_value = q
    q.limit.return_value = q
    q.execute.return_value = MagicMock(data=rows)
    return q


def test_get_articles_for_context_applies_filters():
    """Sage's Tier-1 retrieval: newest-first bounded window with the
    period/theme/source filters applied on the articles table."""
    manager = SupabaseManager()
    manager.available = True
    rows = [{"id": "a1", "title": "Claude released", "published_at": "2025-07-01"}]
    q = _query_chain(rows)
    manager.client = MagicMock()
    manager.client.table.return_value = q

    result = manager.get_articles_for_context(
        theme_filter="Frontier Models & Benchmarks",
        date_from="2025-06-15",
        date_to="2025-07-15",
        source_filter="OpenAI Blog",
        limit=123,
    )

    assert result == rows
    manager.client.table.assert_called_with("articles")
    q.select.assert_called_once()
    q.eq.assert_any_call("theme_name", "Frontier Models & Benchmarks")
    q.eq.assert_any_call("source_name", "OpenAI Blog")
    q.gte.assert_called_with("published_at", "2025-06-15")
    q.lte.assert_called_with("published_at", "2025-07-15")
    q.order.assert_called_with("published_at", desc=True)
    q.limit.assert_called_with(123)


def test_get_articles_for_context_empty():
    """No rows → empty list (not None), so the caller can treat it as no data."""
    manager = SupabaseManager()
    manager.available = True
    manager.client = MagicMock()
    manager.client.table.return_value = _query_chain([])

    assert manager.get_articles_for_context() == []


def test_get_articles_for_context_unavailable():
    """Unavailable manager → None (the caller's 'no Supabase' signal)."""
    manager = SupabaseManager()
    manager.available = False

    assert manager.get_articles_for_context() is None


def _rpc_chain(rows):
    """A self-returning postgrest rpc-builder mock ending in ``rows``."""
    r = MagicMock()
    r.execute.return_value = MagicMock(data=rows)
    return r


def test_get_articles_for_context_with_query_uses_fts_rpc():
    """Phase 2.3: a query routes keyword matching to the match_articles RPC
    (ranked by ts_rank in SQL) instead of the newest-first window."""
    manager = SupabaseManager()
    manager.available = True
    rows = [{"id": "a1", "title": "Claude released", "rank": 0.42}]
    rpc = _rpc_chain(rows)
    manager.client = MagicMock()
    manager.client.rpc.return_value = rpc
    window = _query_chain([])
    manager.client.table.return_value = window

    result = manager.get_articles_for_context(
        theme_filter="Frontier Models & Benchmarks",
        date_from="2025-06-15",
        date_to="2025-07-15",
        source_filter="OpenAI Blog",
        limit=100,
        query="What happened with Claude releases?",
    )

    assert result == rows
    manager.client.rpc.assert_called_once_with("match_articles", {
        "query": "What happened with Claude releases?",
        "result_limit": 100,
        "theme_filter": "Frontier Models & Benchmarks",
        "date_from": "2025-06-15",
        "date_to": "2025-07-15",
        "source_filter": "OpenAI Blog",
    })
    # The window fetch must not run when the RPC already matched rows.
    manager.client.table.assert_not_called()


def test_get_articles_for_context_query_empty_rpc_falls_back_to_window():
    """No FTS match (or an RPC that isn't a list) → newest-first window, so
    Sage always has some Tier-1 evidence."""
    manager = SupabaseManager()
    manager.available = True
    rows = [{"id": "a9", "title": "Newest article"}]
    manager.client = MagicMock()
    manager.client.rpc.return_value = _rpc_chain([])
    manager.client.table.return_value = _query_chain(rows)

    result = manager.get_articles_for_context(query="obscure keywords")

    assert result == rows
    manager.client.table.assert_called_with("articles")


def test_get_articles_for_context_rpc_error_falls_back_to_window():
    """A failing RPC (e.g. match_articles not migrated yet) degrades to the
    window fetch instead of surfacing the error."""
    manager = SupabaseManager()
    manager.available = True
    rows = [{"id": "a9", "title": "Newest article"}]
    rpc = MagicMock()
    rpc.execute.side_effect = Exception("function match_articles not found")
    manager.client = MagicMock()
    manager.client.rpc.return_value = rpc
    manager.client.table.return_value = _query_chain(rows)

    result = manager.get_articles_for_context(query="anything")

    assert result == rows
    manager.client.table.assert_called_with("articles")


def test_get_articles_for_context_blank_query_uses_window():
    """A blank query is treated as no query — the RPC is never called."""
    manager = SupabaseManager()
    manager.available = True
    rows = [{"id": "a9", "title": "Newest article"}]
    manager.client = MagicMock()
    manager.client.table.return_value = _query_chain(rows)

    result = manager.get_articles_for_context(query="   ")

    assert result == rows
    manager.client.rpc.assert_not_called()


def test_insert_sage_evaluation_inserts_row():
    """The Phase 2 gate judge row lands via a plain table insert."""
    manager = SupabaseManager()
    manager.available = True
    insert = MagicMock()
    manager.client = MagicMock()
    manager.client.table.return_value = insert

    row = {"question": "q", "answer_text": "a", "score": 0.9}
    ok = manager.insert_sage_evaluation(row)

    assert ok is True
    manager.client.table.assert_called_with("sage_evaluations")
    insert.insert.assert_called_once_with(row)


def test_insert_sage_evaluation_degrades_to_false():
    """An insert failure (or unavailable manager) returns False, never raises
    — the fire-and-forget judge thread must not surface in the chat."""
    manager = SupabaseManager()
    manager.available = True
    insert = MagicMock()
    insert.insert.side_effect = Exception("table missing")
    manager.client = MagicMock()
    manager.client.table.return_value = insert

    assert manager.insert_sage_evaluation({"score": 1.0}) is False

    offline = SupabaseManager()
    offline.available = False
    assert offline.insert_sage_evaluation({"score": 1.0}) is False


def test_get_sage_evaluations_orders_newest_first():
    manager = SupabaseManager()
    manager.available = True
    rows = [{"id": "e1", "score": 0.9}]
    q = _query_chain(rows)
    manager.client = MagicMock()
    manager.client.table.return_value = q

    result = manager.get_sage_evaluations(limit=50)

    assert result == rows
    manager.client.table.assert_called_with("sage_evaluations")
    q.select.assert_called_once()
    q.order.assert_called_with("created_at", desc=True)
    q.limit.assert_called_with(50)


def test_get_sage_evaluations_unavailable_returns_none():
    manager = SupabaseManager()
    manager.available = False

    assert manager.get_sage_evaluations() is None


# ---------------------------------------------------------------------------
# monthly_syntheses (Phase 6.1)
# ---------------------------------------------------------------------------

def test_get_run_months_dedupes_and_sorts():
    manager = SupabaseManager()
    manager.available = True
    rows = [
        {"run_date": "2026-07-02"}, {"run_date": "2026-06-15"},
        {"run_date": "2026-06-20"}, {"run_date": None},
    ]
    manager.client = MagicMock()
    manager.client.table.return_value = _query_chain(rows)

    assert manager.get_run_months() == ["2026-06", "2026-07"]
    manager.client.table.assert_called_with("trend_runs")


def test_get_run_months_unavailable_returns_none():
    manager = SupabaseManager()
    manager.available = False
    assert manager.get_run_months() is None


def test_get_monthly_syntheses_shapes_and_filters():
    manager = SupabaseManager()
    manager.available = True
    rows = [{"year_month": "2026-06", "theme_name": "T", "macro_summary": "p"}]
    q = _query_chain(rows)
    q.in_.return_value = q
    manager.client = MagicMock()
    manager.client.table.return_value = q

    result = manager.get_monthly_syntheses(year_months=["2026-06"])

    assert result == rows
    manager.client.table.assert_called_with("monthly_syntheses")
    q.in_.assert_called_with("year_month", ["2026-06"])
    q.order.assert_called_with("year_month", desc=True)


def test_get_monthly_syntheses_unavailable_returns_none():
    manager = SupabaseManager()
    manager.available = False
    assert manager.get_monthly_syntheses() is None


def test_upsert_monthly_synthesis_conflict_and_updated_at():
    manager = SupabaseManager()
    manager.available = True
    q = MagicMock()
    q.upsert.return_value = q
    manager.client = MagicMock()
    manager.client.table.return_value = q

    ok = manager.upsert_monthly_synthesis(
        "2026-06", "Theme A", "macro prose",
        [{"milestone": "X"}], 12, "qwen:qwen3.8-flash",
    )

    assert ok is True
    payload = q.upsert.call_args.args[0]
    assert q.upsert.call_args.kwargs["on_conflict"] == "year_month,theme_name"
    assert payload["year_month"] == "2026-06"
    assert payload["theme_name"] == "Theme A"
    assert payload["key_milestones"] == [{"milestone": "X"}]
    assert payload["source_run_count"] == 12
    # updated_at must be stamped on EVERY upsert (staleness rules read it);
    # created_at must be absent so UPDATE preserves the original.
    assert payload["updated_at"]
    assert "created_at" not in payload


def test_upsert_monthly_synthesis_degrades():
    manager = SupabaseManager()
    manager.available = False
    assert manager.upsert_monthly_synthesis("2026-06", "T", "p", [], 1) is False

    manager.available = True
    assert manager.upsert_monthly_synthesis("2026-06", "T", "", [], 1) is False

    q = MagicMock()
    q.upsert.return_value = q
    q.execute.side_effect = RuntimeError("db down")
    manager.client = MagicMock()
    manager.client.table.return_value = q
    assert manager.upsert_monthly_synthesis("2026-06", "T", "p", [], 1) is False


def _month_query_mocks(run_rows, brief_rows):
    """trend_runs + theme_summaries query chains for get_theme_briefs_for_month."""
    runs_q = _query_chain(run_rows)
    sums_q = _query_chain(brief_rows)
    sums_q.in_.return_value = sums_q
    client = MagicMock()
    client.table.side_effect = lambda name: runs_q if name == "trend_runs" else sums_q
    return client, runs_q, sums_q


def test_get_theme_briefs_for_month_bounds_and_join():
    manager = SupabaseManager()
    manager.available = True
    run_rows = [
        {"id": "r2", "run_timestamp": "2026-06-20T12:00:00+00:00", "run_date": "2026-06-20"},
        {"id": "r1", "run_timestamp": "2026-06-02T12:00:00+00:00", "run_date": "2026-06-02"},
    ]
    brief_rows = [
        {"run_id": "r2", "theme_name": "T", "what_is_happening": "later"},
        {"run_id": "r1", "theme_name": "T", "what_is_happening": "earlier"},
    ]
    client, runs_q, sums_q = _month_query_mocks(run_rows, brief_rows)
    manager.client = client

    result = manager.get_theme_briefs_for_month("2026-06")

    # Bounds on run_date (the local DATE key), not run_timestamp.
    runs_q.gte.assert_called_with("run_date", "2026-06-01")
    runs_q.lte.assert_called_with("run_date", "2026-06-30")
    assert [r["run_date"] for r in result] == ["2026-06-02", "2026-06-20"]
    assert result[0]["what_is_happening"] == "earlier"


def test_get_theme_briefs_for_month_chunks_run_ids():
    manager = SupabaseManager()
    manager.available = True
    run_rows = [
        {"id": f"r{i}", "run_timestamp": f"2026-06-{(i % 28) + 1:02d}T12:00:00+00:00",
         "run_date": f"2026-06-{(i % 28) + 1:02d}"}
        for i in range(60)
    ]
    client, _, sums_q = _month_query_mocks(run_rows, [])
    manager.client = client

    manager.get_theme_briefs_for_month("2026-06")

    assert sums_q.in_.call_count == 2  # 50 + 10
    assert len(sums_q.in_.call_args_list[0].args[1]) == 50
    assert len(sums_q.in_.call_args_list[1].args[1]) == 10


def test_get_theme_briefs_for_month_schema_drift_retry():
    manager = SupabaseManager()
    manager.available = True
    run_rows = [{"id": "r1", "run_timestamp": "2026-06-02T12:00:00+00:00", "run_date": "2026-06-02"}]
    brief_rows = [{"run_id": "r1", "theme_name": "T", "what_is_happening": "x"}]
    client, _, sums_q = _month_query_mocks(run_rows, brief_rows)
    sums_q.execute.side_effect = [RuntimeError("column further_reading does not exist"),
                                  MagicMock(data=brief_rows)]
    manager.client = client

    result = manager.get_theme_briefs_for_month("2026-06")

    assert len(result) == 1
    assert sums_q.select.call_count == 2
    first_select = sums_q.select.call_args_list[0].args[0]
    retry_select = sums_q.select.call_args_list[1].args[0]
    assert "further_reading" in first_select
    assert "further_reading" not in retry_select
    assert "generation_source" not in retry_select


def test_get_theme_briefs_for_month_invalid_input_and_degradation():
    manager = SupabaseManager()
    manager.available = True
    manager.client = MagicMock()
    assert manager.get_theme_briefs_for_month("garbage") is None
    assert manager.get_theme_briefs_for_month("2026-13") is None
    assert manager.get_theme_briefs_for_month("") is None

    manager.available = False
    assert manager.get_theme_briefs_for_month("2026-06") is None


def test_get_theme_briefs_for_month_no_runs_returns_empty():
    manager = SupabaseManager()
    manager.available = True
    client, _, _ = _month_query_mocks([], [])
    manager.client = client
    assert manager.get_theme_briefs_for_month("2026-06") == []


# ---------------------------------------------------------------------------
# Sage evaluation mode tag + pipeline activity reads
# ---------------------------------------------------------------------------

def test_insert_sage_evaluation_retries_without_answer_mode_on_drift():
    manager = SupabaseManager()
    manager.available = True
    q = MagicMock()
    q.insert.return_value = q
    q.execute.side_effect = [RuntimeError("column answer_mode does not exist"), MagicMock()]
    manager.client = MagicMock()
    manager.client.table.return_value = q

    ok = manager.insert_sage_evaluation({"question": "q", "score": 0.9, "answer_mode": "deep"})

    assert ok is True
    assert q.insert.call_count == 2
    retry_row = q.insert.call_args_list[1].args[0]
    assert "answer_mode" not in retry_row
    assert retry_row["question"] == "q"


def test_insert_sage_evaluation_no_retry_without_mode_column():
    manager = SupabaseManager()
    manager.available = True
    q = MagicMock()
    q.insert.return_value = q
    q.execute.side_effect = RuntimeError("db down")
    manager.client = MagicMock()
    manager.client.table.return_value = q

    assert manager.insert_sage_evaluation({"question": "q", "score": 0.9}) is False
    assert q.insert.call_count == 1


def test_get_recent_llm_calls_filter_and_shape():
    manager = SupabaseManager()
    manager.available = True
    rows = [{"task_type": "sage_scout", "success": True, "correlation_id": "abc"}]
    q = _query_chain(rows)
    q.in_.return_value = q
    manager.client = MagicMock()
    manager.client.table.return_value = q

    result = manager.get_recent_llm_calls(task_types=["sage_scout", "sage_analyst"], limit=200)

    assert result == rows
    manager.client.table.assert_called_with("llm_calls")
    q.in_.assert_called_with("task_type", ["sage_scout", "sage_analyst"])
    q.order.assert_called_with("created_at", desc=True)
    q.limit.assert_called_with(200)


def test_get_recent_llm_calls_unavailable_returns_none():
    manager = SupabaseManager()
    manager.available = False
    assert manager.get_recent_llm_calls() is None
