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
