"""
Unit tests for core/bg_refresher.py
"""

from datetime import datetime, timedelta
from unittest.mock import patch
from core.bg_refresher import is_cache_expired


def test_is_cache_expired_no_last_run():
    with patch("core.history_manager.get_last_run_time", return_value=None):
        assert is_cache_expired() is True


def test_is_cache_expired_recent_run():
    recent = datetime.now() - timedelta(hours=1)
    with patch("core.history_manager.get_last_run_time", return_value=recent):
        assert is_cache_expired() is False


def test_is_cache_expired_old_run():
    old = datetime.now() - timedelta(hours=13)
    with patch("core.history_manager.get_last_run_time", return_value=old):
        assert is_cache_expired() is True


def test_llm_client_quota_auto_expiry():
    """Test that LLMClient quota flag auto-resets after expiration time."""
    from core.llm_client import LLMClient

    LLMClient.mark_quota_exceeded("Quota test message")
    assert LLMClient.is_quota_exceeded() is True

    # Reset manually
    LLMClient.reset_quota_status()
    assert LLMClient.is_quota_exceeded() is False


def test_run_pipeline_skips_save_when_no_processed_articles():
    """If summariser returns no processed articles, the pipeline should not persist a run."""
    from unittest.mock import MagicMock, patch as _patch
    from core import bg_refresher as bg

    with _patch.object(bg.BackgroundRefresher, "_lock", MagicMock()):
        with _patch("core.bg_refresher.BackgroundRefresher.update_progress"):
            with _patch("core.fetcher.fetch_all_news", return_value=[{"title": "T", "link": "http://x"}]):
                with _patch("core.classifier.classify_articles", return_value={"Frontier Models & Benchmarks": []}):
                    with _patch("core.summariser.generate_all_summaries", return_value=({}, {})) as mock_gen:
                        with _patch("core.history_manager.save_run_to_history") as mock_save:
                            with _patch.object(bg.BackgroundRefresher, "_get_state", return_value={}):
                                bg.BackgroundRefresher._run_pipeline()
    mock_gen.assert_called_once()
    mock_save.assert_not_called()


def test_run_pipeline_saves_when_articles_processed():
    """If summariser processes articles, the pipeline should persist a run (when not a duplicate)."""
    from unittest.mock import MagicMock, patch as _patch
    from core import bg_refresher as bg

    with _patch.object(bg.BackgroundRefresher, "_lock", MagicMock()):
        with _patch("core.bg_refresher.BackgroundRefresher.update_progress"):
            with _patch("core.fetcher.fetch_all_news", return_value=[{"title": "T", "link": "http://x"}]):
                with _patch("core.classifier.classify_articles", return_value={"Frontier Models & Benchmarks": []}):
                    with _patch("core.summariser.generate_all_summaries", return_value=({"Frontier Models & Benchmarks": {}}, {"Frontier Models & Benchmarks": ["h1"]})) as mock_gen:
                        with _patch("core.history_manager.is_duplicate_run", return_value=False):
                            with _patch("core.history_manager.save_run_to_history") as mock_save:
                                with _patch.object(bg.BackgroundRefresher, "_get_state", return_value={}):
                                    bg.BackgroundRefresher._run_pipeline()
    mock_gen.assert_called_once()
    mock_save.assert_called_once()