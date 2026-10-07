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
