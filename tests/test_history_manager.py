"""Tests for the cloud-first run loader in core/history_manager.py.

The UI used to read only ``history.json``.  A background run that persisted
to Supabase from another process (or an ephemeral deployment) could leave the
local cache months behind, so Deep Dive rendered stale summaries with no
provenance.  ``load_latest_run_data()`` now prefers the newer Supabase run
and caches it locally.
"""

import json
from unittest.mock import MagicMock

import pytest

import core.history_manager as history


@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "HISTORY_JSON", tmp_path / "history.json")
    monkeypatch.setattr(history, "_history_cache", None)
    yield
    monkeypatch.setattr(history, "_history_cache", None)


def _cloud_manager():
    manager = MagicMock()
    manager.is_available.return_value = True
    manager.get_latest_run.return_value = {
        "id": "run-cloud",
        "run_timestamp": "2026-11-10T16:39:31+00:00",
        "run_date": "2026-11-10",
        "article_fingerprint": "fp-cloud",
    }
    manager.get_summaries_by_run.return_value = {
        "Frontier Models & Benchmarks": {
            "theme_name": "Frontier Models & Benchmarks",
            "what_is_happening": "Qwen shipped a new model.",
            "why_it_matters": "It changes the price curve.",
            "generation_source": "qwen:qwen3.8-flash",
            "generation_log": json.dumps(
                {
                    "included_count": 5,
                    "total_articles": 10,
                    "skipped_count": 5,
                    "skipped_hashes": ["h1"],
                }
            ),
        }
    }
    manager.get_articles_for_run.return_value = [
        {
            "id": "a1",
            "theme_name": "Frontier Models & Benchmarks",
            "title": "Qwen 3.8 Flash",
            "summary": "A new model.",
            "source_name": "Qwen Blog",
            "link": "https://example.com/qwen",
            "published_at": "2026-11-10T10:00:00+00:00",
        }
    ]
    return manager


def test_uses_cloud_run_when_local_is_stale(monkeypatch):
    local = {
        "timestamp": "2026-05-21 00:11:56",
        "data": {"summaries": {}, "full_articles": [], "themed_articles": {}},
    }
    monkeypatch.setattr(history, "get_last_run", lambda: local)
    manager = _cloud_manager()
    monkeypatch.setattr(
        "core.supabase_client.get_supabase_manager", lambda: manager
    )

    result = history.load_latest_run_data()

    assert result["source"] == "supabase"
    data = result["data"]
    summary = data["summaries"]["Frontier Models & Benchmarks"]
    assert summary["_source"] == "qwen:qwen3.8-flash"
    assert summary["included_count"] == 5
    assert summary["total_articles"] == 10
    assert summary["_generation_log"]["skipped_count"] == 5

    assert len(data["full_articles"]) == 1
    article = data["themed_articles"]["Frontier Models & Benchmarks"][0]
    assert article["theme"] == "Frontier Models & Benchmarks"
    assert article["source"] == "Qwen Blog"
    assert article["published_date"] == "2026-11-10T10:00:00+00:00"

    # The cloud run is cached locally so the next load is instant.
    cached = json.loads(history.HISTORY_JSON.read_text(encoding="utf-8"))
    assert len(cached) == 1
    assert list(cached.values())[0]["counts"]["Frontier Models & Benchmarks"] == 1


def test_keeps_local_run_when_newer(monkeypatch):
    local = {
        "timestamp": "2026-11-11 01:00:00",
        "data": {"summaries": {"Local": {}}, "full_articles": [], "themed_articles": {}},
    }
    monkeypatch.setattr(history, "get_last_run", lambda: local)
    manager = _cloud_manager()
    manager.get_latest_run.return_value["run_timestamp"] = "2026-11-10T16:00:00+00:00"
    monkeypatch.setattr(
        "core.supabase_client.get_supabase_manager", lambda: manager
    )

    result = history.load_latest_run_data()

    assert result is local
    assert result.get("source") is None


def test_falls_back_to_local_when_cloud_fails(monkeypatch):
    local = {
        "timestamp": "2026-05-21 00:11:56",
        "data": {"summaries": {}, "full_articles": [], "themed_articles": {}},
    }
    monkeypatch.setattr(history, "get_last_run", lambda: local)
    manager = MagicMock()
    manager.is_available.return_value = True
    manager.get_latest_run.side_effect = RuntimeError("supabase down")
    monkeypatch.setattr(
        "core.supabase_client.get_supabase_manager", lambda: manager
    )

    assert history.load_latest_run_data() is local