"""Tests for run fingerprinting and duplicate-run guard."""

import hashlib
import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from core.history_manager import compute_run_fingerprint, is_duplicate_run


class TestComputeRunFingerprint:
    def test_stable_for_same_articles(self):
        articles = [
            {"content_hash": "h1", "title": "A", "link": "http://a"},
            {"content_hash": "h2", "title": "B", "link": "http://b"},
            {"content_hash": "h3", "title": "C", "link": "http://c"},
        ]
        fp1 = compute_run_fingerprint(articles)
        fp2 = compute_run_fingerprint(articles)
        assert fp1 == fp2
        assert len(fp1) == 64  # SHA-256 hex

    def test_different_set_different_fingerprint(self):
        a = [{"content_hash": "h1", "title": "A", "link": "http://a"}]
        b = [{"content_hash": "h2", "title": "B", "link": "http://b"}]
        assert compute_run_fingerprint(a) != compute_run_fingerprint(b)

    def test_empty_articles(self):
        assert compute_run_fingerprint([]) == "empty"

    def test_fallback_to_title_link(self):
        articles = [{"title": "A", "link": "http://a"}]
        fp = compute_run_fingerprint(articles)
        expected = hashlib.sha256(
            hashlib.md5("http://aA".encode()).hexdigest().encode()
        ).hexdigest()
        assert fp == expected


class TestIsDuplicateRun:
    def test_no_fingerprint_not_duplicate(self):
        assert is_duplicate_run("") is False

    def test_supabase_duplicate_within_window(self, tmp_path):
        fake_supabase = MagicMock()
        fake_supabase.is_available.return_value = True
        fake_run = {
            "article_fingerprint": "abc123",
            "run_timestamp": (datetime.now() - timedelta(minutes=10)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }
        fake_supabase.get_latest_run.return_value = fake_run
        with patch("core.supabase_client.get_supabase_manager", return_value=fake_supabase):
            assert is_duplicate_run("abc123") is True

    def test_supabase_different_fingerprint(self, tmp_path):
        fake_supabase = MagicMock()
        fake_supabase.is_available.return_value = True
        fake_run = {
            "article_fingerprint": "abc123",
            "run_timestamp": (datetime.now() - timedelta(minutes=10)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }
        fake_supabase.get_latest_run.return_value = fake_run
        with patch("core.supabase_client.get_supabase_manager", return_value=fake_supabase):
            assert is_duplicate_run("def456") is False

    def test_supabase_outside_window(self, tmp_path):
        fake_supabase = MagicMock()
        fake_supabase.is_available.return_value = True
        fake_run = {
            "article_fingerprint": "abc123",
            "run_timestamp": (datetime.now() - timedelta(minutes=60)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }
        fake_supabase.get_latest_run.return_value = fake_run
        with patch("core.supabase_client.get_supabase_manager", return_value=fake_supabase):
            assert is_duplicate_run("abc123") is False
