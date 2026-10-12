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
