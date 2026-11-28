"""Tests for BLOG.md parsing and search (core/blog_index.py)."""
from pathlib import Path

from core.blog_index import (
    collect_labels,
    filter_entries,
    search_entries,
    split_blog_entries,
)

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


# ---------------------------------------------------------------------------
# Labels (filter metadata)
# ---------------------------------------------------------------------------

_LABELED_SAMPLE = """# Blog

## 2026-11-28 — Visual tour
Labels: explainer, sage, retrieval

The tour body.

## 2026-11-27 — Fusion lesson
Labels: sage, evals

The fusion body.

## 2026-03-03 — Day 1

No labels here.
"""


def test_labels_parsed_and_stripped_from_body():
    entries = split_blog_entries(_LABELED_SAMPLE)
    assert entries[0]["labels"] == ["explainer", "sage", "retrieval"]
    assert entries[0]["body"] == "The tour body."
    assert "Labels:" not in entries[0]["body"]
    assert entries[1]["labels"] == ["sage", "evals"]


def test_labels_absent_and_case_insensitive():
    entries = split_blog_entries(_LABELED_SAMPLE)
    assert entries[2]["labels"] == []
    assert entries[2]["body"] == "No labels here."
    e = split_blog_entries("## T\nLABELS: Sage, EVALS\nbody")[0]
    assert e["labels"] == ["sage", "evals"]
    # Entries without the line stay backwards compatible.
    assert split_blog_entries(_SAMPLE)[0]["labels"] == []


def test_collect_labels_sorted_unique():
    entries = split_blog_entries(_LABELED_SAMPLE)
    assert collect_labels(entries) == ["evals", "explainer", "retrieval", "sage"]


def test_filter_entries_labels_or_semantics():
    entries = split_blog_entries(_LABELED_SAMPLE)
    # ANY selected label matches.
    assert len(filter_entries(entries, labels=["evals", "explainer"])) == 2
    assert len(filter_entries(entries, labels=["retrieval"])) == 1
    assert filter_entries(entries, labels=["nonexistent"]) == []
    # Empty/None labels = no label constraint.
    assert filter_entries(entries, labels=[]) == entries
    assert filter_entries(entries) == entries


def test_filter_entries_combines_search_and_labels():
    entries = split_blog_entries(_LABELED_SAMPLE)
    # Search AND labels intersect: "body" appears in both labeled entries,
    # but only the fusion one carries "evals".
    assert len(filter_entries(entries, "body", ["evals"])) == 1
    assert filter_entries(entries, "tour", ["evals"]) == []
    assert len(filter_entries(entries, "tour", ["explainer"])) == 1


def test_real_blog_all_entries_labeled():
    blog = Path(__file__).resolve().parent.parent / "BLOG.md"
    entries = split_blog_entries(blog.read_text(encoding="utf-8"))
    assert all(e["labels"] for e in entries)
    taxonomy = {
        "explainer", "sage", "retrieval", "pipeline", "gateway",
        "evals", "supabase", "reliability", "ui",
    }
    assert set(collect_labels(entries)) <= taxonomy
    assert all("Labels:" not in e["body"] for e in entries)