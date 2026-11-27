"""Tests for BLOG.md parsing and search (core/blog_index.py)."""
from pathlib import Path

from core.blog_index import split_blog_entries, search_entries

_SAMPLE = """# AI Pulse — Development Blog

Preamble that should be dropped.

## 2026-11-10 to 2026-11-11 — Qwen Cloud takes the stage

Ollama went paid. We migrated the gateway.

## 2026-03-03 — Day 1: AI Pulse begins

The first commit.
"""


def test_split_basic():
    entries = split_blog_entries(_SAMPLE)
    assert len(entries) == 2
    assert entries[0]["title"] == "Qwen Cloud takes the stage"
    assert entries[0]["date_label"] == "2026-11-10 to 2026-11-11"
    assert "migrated the gateway" in entries[0]["body"]
    assert entries[1]["title"] == "Day 1: AI Pulse begins"
    assert entries[1]["date_label"] == "2026-03-03"


def test_split_empty_and_preamble_only():
    assert split_blog_entries("") == []
    assert split_blog_entries("# Just a preamble\n\ntext") == []


def test_split_heading_without_date():
    entries = split_blog_entries("## A plain heading\n\nbody text")
    assert entries[0]["title"] == "A plain heading"
    assert entries[0]["date_label"] == ""


def test_search_and_semantics():
    entries = split_blog_entries(_SAMPLE)
    assert len(search_entries(entries, "qwen gateway")) == 1
    # "qwen" AND "day" — only entry 2 has "Day", entry 1 has "Qwen": no match
    assert search_entries(entries, "qwen day") == []


def test_search_case_insensitive_and_partial():
    entries = split_blog_entries(_SAMPLE)
    assert len(search_entries(entries, "MIGRATED")) == 1
    assert len(search_entries(entries, "ollam")) == 1  # substring


def test_search_empty_returns_all():
    entries = split_blog_entries(_SAMPLE)
    assert search_entries(entries, "") == entries
    assert search_entries(entries, "   ") == entries


def test_real_blog_parses():
    blog = Path(__file__).resolve().parent.parent / "BLOG.md"
    entries = split_blog_entries(blog.read_text(encoding="utf-8"))
    assert len(entries) >= 17
    assert any("Qwen Cloud" in e["title"] for e in entries)
    assert all(e["body"] for e in entries)
    # Every dated entry parsed its date label.
    assert all(e["date_label"] for e in entries)
    # File order is newest first: leading dates never increase.
    leading = [e["date_label"][:10] for e in entries]
    assert leading == sorted(leading, reverse=True)