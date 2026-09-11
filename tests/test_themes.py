"""Tests for runtime keyword management in config/themes.py."""
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _reset_themes(monkeypatch, tmp_path):
    """Reload themes.py with a clean, temporary custom_keywords.json."""
    import importlib

    from config import themes

    # Reload first to reset any state from previous tests.
    importlib.reload(themes)
    # Then patch the overlay file to a temp path so tests don't interfere.
    monkeypatch.setattr(themes, "CUSTOM_KEYWORDS_FILE", tmp_path / "custom_keywords.json")
    yield
    importlib.reload(themes)


def test_add_keywords_to_theme_updates_in_memory_and_persists(tmp_path):
    from config.themes import THEMES, add_keywords_to_theme, CUSTOM_KEYWORDS_FILE

    theme = "Agentic Systems & DevTools"
    assert add_keywords_to_theme(theme, {"automation-pattern-xyz": 3}) is True

    # In-memory update is immediate.
    assert THEMES[theme]["keywords"]["automation-pattern-xyz"] == 3

    # Disk overlay is written.
    data = json.loads(CUSTOM_KEYWORDS_FILE.read_text(encoding="utf-8"))
    assert data[theme]["automation-pattern-xyz"] == 3


def test_add_keywords_to_theme_succeeds_in_memory_when_disk_is_read_only(tmp_path):
    from config.themes import THEMES, add_keywords_to_theme, CUSTOM_KEYWORDS_FILE

    # Simulate a read-only filesystem by making the overlay file path a directory,
    # so any open(..., "w") inside save_custom_keywords() raises IsADirectoryError.
    CUSTOM_KEYWORDS_FILE.mkdir()

    theme = "Agentic Systems & DevTools"
    # Should not raise; in-memory update must still happen.
    assert add_keywords_to_theme(theme, {"read-only-term": 2}) is True
    assert THEMES[theme]["keywords"]["read-only-term"] == 2


def test_remove_keyword_from_theme_updates_in_memory_and_persists(tmp_path):
    from config.themes import THEMES, add_keywords_to_theme, remove_keyword_from_theme, CUSTOM_KEYWORDS_FILE

    theme = "Agentic Systems & DevTools"
    add_keywords_to_theme(theme, {"temporary-keyword": 3})
    assert remove_keyword_from_theme(theme, "temporary-keyword") is True

    assert "temporary-keyword" not in THEMES[theme]["keywords"]
    data = json.loads(CUSTOM_KEYWORDS_FILE.read_text(encoding="utf-8"))
    assert "temporary-keyword" not in data.get(theme, {})


def test_add_keywords_to_theme_returns_false_for_unknown_theme(tmp_path):
    from config.themes import add_keywords_to_theme

    assert add_keywords_to_theme("Not a real theme", {"foo": 1}) is False


class FakeSupabaseResponse:
    def __init__(self, data):
        self.data = data


class FakeSupabaseClient:
    def __init__(self, data=None):
        self.data = data or []
        self.last_upsert = None

    def table(self, name):
        return FakeTable(self, name, self.data)


class FakeTable:
    def __init__(self, client, name, data):
        self.client = client
        self.name = name
        self.data = data

    def select(self, *args):
        return FakeQuery(self, self.data)

    def upsert(self, rows, on_conflict=None):
        self.client.last_upsert = rows
        return FakeQuery(self, [])


class FakeQuery:
    def __init__(self, table, data):
        self.table = table
        self.data = data

    def execute(self):
        return FakeSupabaseResponse(self.data)


class FakeSupabaseManager:
    def __init__(self, data=None):
        self.client = FakeSupabaseClient(data)
        self._available = True

    def is_available(self):
        return self._available


def test_load_custom_keywords_prefers_supabase(tmp_path):
    from config import themes

    fake_data = [
        {"theme_name": "Agentic Systems & DevTools", "keywords": {"supabase-term": 3}}
    ]
    manager = FakeSupabaseManager(fake_data)

    with patch.object(themes, "_get_supabase_manager", return_value=manager):
        data = themes.load_custom_keywords()

    assert data["Agentic Systems & DevTools"]["supabase-term"] == 3


def test_save_custom_keywords_writes_to_supabase(tmp_path):
    from config import themes

    manager = FakeSupabaseManager()

    custom_data = {
        "Agentic Systems & DevTools": {"supabase-term": 3},
    }

    with patch.object(themes, "_get_supabase_manager", return_value=manager):
        success = themes.save_custom_keywords(custom_data)

    assert success is True
    assert manager.client.last_upsert is not None
    rows = manager.client.last_upsert
    assert any(r["theme_name"] == "Agentic Systems & DevTools" for r in rows)


def test_load_custom_keywords_falls_back_to_local_file(tmp_path):
    from config import themes
    from config.themes import load_custom_keywords, CUSTOM_KEYWORDS_FILE

    # No Supabase available, but local file has data
    local_data = {"Agentic Systems & DevTools": {"local-term": 2}}
    CUSTOM_KEYWORDS_FILE.write_text(json.dumps(local_data), encoding="utf-8")

    manager = FakeSupabaseManager()
    manager._available = False

    with patch.object(themes, "_get_supabase_manager", return_value=manager):
        data = load_custom_keywords()

    assert data["Agentic Systems & DevTools"]["local-term"] == 2