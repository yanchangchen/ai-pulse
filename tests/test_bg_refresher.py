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
    old = datetime.now() - timedelta(hours=7)
    with patch("core.history_manager.get_last_run_time", return_value=old):
        assert is_cache_expired() is True
