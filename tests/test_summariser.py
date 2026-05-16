"""Tests for the summariser module — specifically parse_further_reading."""

import pytest
from core.summariser import parse_further_reading


class TestParseFurtherReading:
    """Test the further-reading parser."""

    def test_standard_pipe_format(self):
        text = "- Article Title | Source | https://example.com | Worth reading because X"
        result = parse_further_reading(text)
        assert len(result) == 1
        assert result[0]['title'] == "Article Title"
        assert result[0]['source'] == "Source"
        assert result[0]['url'] == "https://example.com"
        assert result[0]['reason'] == "Worth reading because X"

    def test_multiple_entries(self):
        text = (
            "- Title 1 | Source A | https://a.com | Reason A\n"
            "- Title 2 | Source B | https://b.com | Reason B\n"
            "- Title 3 | Source C | https://c.com | Reason C"
        )
        result = parse_further_reading(text)
        assert len(result) == 3

    def test_asterisk_bullets(self):
        text = "* Article | Source | https://url.com | Great read"
        result = parse_further_reading(text)
        assert len(result) == 1
        assert result[0]['title'] == "Article"

    def test_no_reason_field(self):
        text = "- Title | Source | https://url.com"
        result = parse_further_reading(text)
        assert len(result) == 1
        assert result[0]['reason'] == ""

    def test_empty_string(self):
        assert parse_further_reading("") == []

    def test_no_pipe_lines_skipped(self):
        text = "This is just a sentence with no pipes."
        result = parse_further_reading(text)
        assert result == []

    def test_blank_lines_ignored(self):
        text = "\n\n- Title | Source | https://url.com | Reason\n\n"
        result = parse_further_reading(text)
        assert len(result) == 1
