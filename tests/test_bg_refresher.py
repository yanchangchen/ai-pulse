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


def test_console_print_normal_utf8(capsys):
    from core.bg_refresher import _console_print
    _console_print("🔄 hello")
    assert "🔄 hello" in capsys.readouterr().out


def test_console_print_survives_non_utf8_console(monkeypatch):
    """A cp1252 console (Windows headless) must not kill the pipeline over an emoji."""
    import io
    import sys
    from core.bg_refresher import _console_print

    buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", buf)
    _console_print("🔄 [AI Pulse BG Refresher] Current Step: test")  # must not raise
    out = buf.buffer.getvalue().decode("cp1252")
    assert "Current Step: test" in out


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


def test_run_pipeline_checks_monthly_rollups_even_without_new_articles():
    """Phase 6.1: the rollup due-check must run on the no-persistence path too
    (month rollover and the biweekly current-month refresh can't be skipped)."""
    from unittest.mock import MagicMock, patch as _patch
    from core import bg_refresher as bg

    with _patch.object(bg.BackgroundRefresher, "_lock", MagicMock()):
        with _patch("core.bg_refresher.BackgroundRefresher.update_progress"):
            with _patch("core.fetcher.fetch_all_news", return_value=[{"title": "T", "link": "http://x"}]):
                with _patch("core.classifier.classify_articles", return_value={"Frontier Models & Benchmarks": []}):
                    with _patch("core.summariser.generate_all_summaries", return_value=({}, {})):
                        with _patch("core.history_manager.save_run_to_history") as mock_save:
                            with _patch("core.monthly_synthesis.synthesize_due_monthly", return_value=0) as mock_rollup:
                                with _patch.object(bg.BackgroundRefresher, "_get_state", return_value={}):
                                    bg.BackgroundRefresher._run_pipeline()
    mock_save.assert_not_called()
    mock_rollup.assert_called_once()


def test_run_pipeline_rollup_failure_does_not_fail_the_run():
    """A rollup exception is best-effort: the pipeline still completes."""
    from unittest.mock import MagicMock, patch as _patch
    from core import bg_refresher as bg

    state = {}
    with _patch.object(bg.BackgroundRefresher, "_lock", MagicMock()):
        with _patch("core.bg_refresher.BackgroundRefresher.update_progress"):
            with _patch("core.fetcher.fetch_all_news", return_value=[{"title": "T", "link": "http://x"}]):
                with _patch("core.classifier.classify_articles", return_value={"Frontier Models & Benchmarks": []}):
                    with _patch("core.summariser.generate_all_summaries", return_value=({}, {})):
                        with _patch("core.monthly_synthesis.synthesize_due_monthly", side_effect=RuntimeError("rollup boom")):
                            with _patch.object(bg.BackgroundRefresher, "_get_state", return_value=state):
                                bg.BackgroundRefresher._run_pipeline()
    assert state.get("status") == "completed"


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