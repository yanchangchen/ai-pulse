"""Tests for the processed-articles tracker."""

import json
import pytest
from unittest.mock import patch, MagicMock

from core import processed_articles as pa
from core.processed_articles import article_hash


class TestArticleHash:
    def test_uses_content_hash(self):
        article = {"content_hash": "abc123", "title": "T", "link": "http://x"}
        assert article_hash(article) == "abc123"

    def test_falls_back_to_title_link(self):
        import hashlib
        article = {"title": "Hello", "link": "http://example.com"}
        expected = hashlib.md5(f"{article['link']}{article['title']}".encode()).hexdigest()
        assert article_hash(article) == expected


class TestFilterUnprocessed:
    def test_all_unprocessed_when_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "LOCAL_PROCESSED_FILE", tmp_path / "processed.json")
        articles = [
            {"content_hash": "h1", "title": "A"},
            {"content_hash": "h2", "title": "B"},
        ]
        assert pa.filter_unprocessed("Frontier Models & Benchmarks", articles) == articles

    def test_filters_processed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "LOCAL_PROCESSED_FILE", tmp_path / "processed.json")
        # Seed processed set for this theme
        pa.mark_processed("Frontier Models & Benchmarks", ["h1"])
        articles = [
            {"content_hash": "h1", "title": "A"},
            {"content_hash": "h2", "title": "B"},
        ]
        result = pa.filter_unprocessed("Frontier Models & Benchmarks", articles)
        assert len(result) == 1
        assert result[0]["content_hash"] == "h2"


class TestMarkProcessed:
    def test_local_persists(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "LOCAL_PROCESSED_FILE", tmp_path / "processed.json")
        pa.mark_processed("Agentic Systems & DevTools", ["a", "b"])
        data = json.loads((tmp_path / "processed.json").read_text())
        assert "a" in data["Agentic Systems & DevTools"]
        assert "b" in data["Agentic Systems & DevTools"]

    def test_supabase_preferred(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "LOCAL_PROCESSED_FILE", tmp_path / "processed.json")
        fake_supabase = MagicMock()
        fake_supabase.is_available.return_value = True
        fake_supabase.get_processed_hashes.return_value = set()
        with patch("core.supabase_client.get_supabase_manager", return_value=fake_supabase):
            pa.mark_processed("Hardware, Compute & LLMOps", ["h1"])
        fake_supabase.mark_processed.assert_called_once_with(
            "Hardware, Compute & LLMOps", ["h1"]
        )

    def test_supabase_read_used(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "LOCAL_PROCESSED_FILE", tmp_path / "processed.json")
        fake_supabase = MagicMock()
        fake_supabase.is_available.return_value = True
        fake_supabase.get_processed_hashes.return_value = {"h1"}
        articles = [
            {"content_hash": "h1", "title": "A"},
            {"content_hash": "h2", "title": "B"},
        ]
        with patch("core.supabase_client.get_supabase_manager", return_value=fake_supabase):
            result = pa.filter_unprocessed("Hardware, Compute & LLMOps", articles)
        assert len(result) == 1
        assert result[0]["content_hash"] == "h2"
