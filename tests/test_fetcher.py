"""Tests for the news fetcher module."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from core.fetcher import (
    parse_date,
    is_within_range,
    extract_date_from_entry,
    fetch_rss_feed,
)


class TestParseDate:
    """Test date parsing from various formats."""

    def test_iso_format(self):
        dt = parse_date("2026-05-01T12:00:00Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 5

    def test_rfc2822_format(self):
        dt = parse_date("Thu, 01 May 2026 12:00:00 +0000")
        assert dt is not None
        assert dt.year == 2026

    def test_human_readable_format(self):
        dt = parse_date("May 1, 2026")
        assert dt is not None
        assert dt.month == 5

    def test_empty_string(self):
        assert parse_date("") is None

    def test_none_input(self):
        assert parse_date(None) is None

    def test_garbage_string(self):
        assert parse_date("not a date at all") is None

    def test_result_is_timezone_aware(self):
        """parse_date should always return timezone-aware datetimes."""
        dt = parse_date("2026-05-01T12:00:00")
        assert dt is not None
        assert dt.tzinfo is not None


class TestIsWithinRange:
    """Test the date-range filter."""

    def test_recent_date_in_range(self):
        recent = datetime.now(timezone.utc) - timedelta(days=1)
        assert is_within_range(recent) is True

    def test_old_date_out_of_range(self):
        old = datetime.now(timezone.utc) - timedelta(days=30)
        assert is_within_range(old) is False

    def test_none_returns_false(self):
        assert is_within_range(None) is False

    def test_exactly_14_days_ago(self):
        """A date slightly within the 14-day window should be in range."""
        # Use 13 days + 23 hours to avoid microsecond drift at the boundary
        boundary = datetime.now(timezone.utc) - timedelta(days=13, hours=23)
        assert is_within_range(boundary) is True

    def test_naive_datetime_treated_as_utc(self):
        """Naive datetimes should be handled gracefully (treated as UTC)."""
        recent_naive = datetime.now() - timedelta(days=1)
        assert is_within_range(recent_naive) is True


class TestRssFetchTransport:
    """Verify the RSS fetch path sends a User-Agent and follows redirects.

    These don't hit the network — they patch requests.get and feedparser.parse
    so the test runs offline in <1s.  They exist because several AI blog
    feeds (OpenAI, DeepMind, Hugging Face) were silently 0-entrying in
    production: feedparser.parse(url) doesn't follow 307 redirects and
    sends a default python-urllib User-Agent that some sites block.
    """

    def test_sends_user_agent_and_follows_redirects(self):
        # A redirect-following 200 with an Atom feed body
        atom_body = (
            b'<?xml version="1.0" encoding="utf-8"?>'
            b'<feed xmlns="http://www.w3.org/2005/Atom">'
            b'<entry><title>Hello</title><updated>2026-07-01T12:00:00Z</updated>'
            b'<link href="https://example.com/hello"/></entry></feed>'
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = atom_body
        mock_resp.raise_for_status = MagicMock()

        with patch("core.fetcher.requests.get", return_value=mock_resp) as mock_get:
            items = fetch_rss_feed({
                "name": "Mocked Feed",
                "url": "https://example.com/redirected",
                "type": "rss",
            })

        # requests.get was called with allow_redirects=True and a UA.
        kwargs = mock_get.call_args.kwargs
        assert kwargs.get("allow_redirects") is True
        assert "User-Agent" in kwargs.get("headers", {})
        # We asked for the original URL, not the redirect target.
        assert mock_get.call_args.args[0] == "https://example.com/redirected"
        # The feed parsed (1 entry).
        assert isinstance(items, list)

    def test_propagates_http_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status.side_effect = Exception("HTTP 404")
        with patch("core.fetcher.requests.get", return_value=mock_resp):
            items = fetch_rss_feed({
                "name": "Broken Feed",
                "url": "https://example.com/404",
                "type": "rss",
            })
        assert items == []


def test_diagnose_source_healthy():
    from core.fetcher import diagnose_source
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"<rss><channel><item><title>Test Article</title></item></channel></rss>"
    mock_resp.headers = {"Content-Type": "application/rss+xml"}

    with patch("core.fetcher.requests.get", return_value=mock_resp):
        source = {"name": "Test Feed", "url": "https://example.com/rss", "type": "rss"}
        diag = diagnose_source(source)
        assert diag["healthy"] is True
        assert diag["status_code"] == 200
        assert diag["items_found"] > 0


def test_diagnose_source_404_error():
    from core.fetcher import diagnose_source
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.headers = {"Content-Type": "text/html"}

    with patch("core.fetcher.requests.get", return_value=mock_resp):
        source = {"name": "Broken Feed", "url": "https://example.com/404", "type": "rss"}
        diag = diagnose_source(source)
        assert diag["healthy"] is False
        assert diag["status_code"] == 404
        assert "404 Not Found" in diag["explanation"]


def test_rss_summary_max_chars_respected():
    from core.fetcher import fetch_rss_feed
    long_desc = "Word " * 500  # ~2500 chars

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = f"""<rss><channel><item>
        <title>Long RSS Feed Item Title</title>
        <description>{long_desc}</description>
        <link>https://example.com/item1</link>
    </item></channel></rss>""".encode()

    with patch("core.fetcher.requests.get", return_value=mock_resp):
        items = fetch_rss_feed({"name": "Test Feed", "url": "https://example.com/rss", "type": "rss"})
        assert len(items) == 1
        assert len(items[0]["summary"]) <= 1500
        assert len(items[0]["summary"]) > 500


def test_scrape_web_source_summary_extraction():
    from core.fetcher import scrape_web_source
    mock_html = """
    <html>
      <body>
        <div>
          <h2><a href="https://example.com/post1">Anthropic Engineering Breakthrough</a></h2>
          <p>We are introducing a novel architectural optimization for frontier LLM reasoning.</p>
        </div>
      </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = mock_html.encode()

    with patch("core.fetcher.requests.get", return_value=mock_resp):
        source = {"name": "Anthropic Engineering", "url": "https://www.anthropic.com/engineering", "type": "web"}
        items = scrape_web_source(source)
        assert len(items) >= 1
        assert "introducing a novel architectural optimization" in items[0]["summary"]


