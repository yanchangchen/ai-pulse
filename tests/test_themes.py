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
