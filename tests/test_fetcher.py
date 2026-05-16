"""Tests for the news fetcher module."""

import pytest
from datetime import datetime, timezone, timedelta
from core.fetcher import parse_date, is_within_range, extract_date_from_entry


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
