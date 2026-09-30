"""Tests for core/sage_conversations.py — saving, listing, retrieving, deleting,
and exporting persistent Sage conversation threads."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile
import json

from core.sage_conversations import (
    save_conversation,
    get_conversation,
    list_saved_conversations,
    delete_conversation,
    export_conversation_markdown,
)


@pytest.fixture
def temp_conv_file(tmp_path):
    conv_file = tmp_path / "sage_conversations.json"
    with patch("core.sage_conversations.CONVERSATIONS_FILE", conv_file):
        yield conv_file


def test_save_and_get_conversation(temp_conv_file):
    messages = [
        {"role": "user", "content": "What are the latest breakthroughs in agentic AI?"},
        {"role": "assistant", "content": "1. Factual Account... 2. My read on this..."},
    ]
    cid = save_conversation(
        messages=messages,
        title="Agentic Breakthroughs",
        theme_filter="Agentic Systems",
        period_label="Last 30 Days",
    )
    assert cid is not None

    conv = get_conversation(cid)
    assert conv is not None
    assert conv["title"] == "Agentic Breakthroughs"
    assert conv["theme_filter"] == "Agentic Systems"
    assert conv["period_label"] == "Last 30 Days"
    assert len(conv["messages"]) == 2


def test_auto_generate_title(temp_conv_file):
    messages = [
        {"role": "user", "content": "Explain how Nous Research trained the TST model on contiguous token bags"},
    ]
    cid = save_conversation(messages=messages)
    conv = get_conversation(cid)
    assert conv is not None
    assert "Nous Research" in conv["title"]


def test_list_saved_conversations(temp_conv_file):
    save_conversation(
        messages=[{"role": "user", "content": "Topic 1"}],
        title="Topic 1",
    )
    save_conversation(
        messages=[{"role": "user", "content": "Topic 2"}],
        title="Topic 2",
    )

    convs = list_saved_conversations()
    assert len(convs) == 2
    titles = [c["title"] for c in convs]
    assert "Topic 1" in titles
    assert "Topic 2" in titles


def test_delete_conversation(temp_conv_file):
    cid = save_conversation(
        messages=[{"role": "user", "content": "To be deleted"}],
        title="Delete Me",
    )
    assert get_conversation(cid) is not None

    deleted = delete_conversation(cid)
    assert deleted is True
    assert get_conversation(cid) is None


def test_export_conversation_markdown(temp_conv_file):
    messages = [
        {"role": "user", "content": "What happened with Claude Opus 4.7?"},
        {"role": "assistant", "content": "Anthropic announced Claude Opus 4.7 on July 10."},
    ]
    md = export_conversation_markdown(
        title="Claude Opus Inquiry",
        messages=messages,
        theme_filter="AI Models",
        period_label="July 2026",
    )
    assert "# 🔮 Sage Research Report: Claude Opus Inquiry" in md
    assert "Claude Opus 4.7" in md
    assert "👤 User" in md
    assert "🔮 Sage" in md
    assert "AI Models" in md
