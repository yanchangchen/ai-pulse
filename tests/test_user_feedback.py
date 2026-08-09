"""
Tests for user feedback and published_date fallbacks.
"""

from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
from core.fetcher import fetch_rss_feed
from core.supabase_client import SupabaseManager, _save_local_feedback_item, _get_local_feedback, _delete_local_feedback


def test_fetch_rss_feed_published_date_fallback():
    """Test that fetch_rss_feed defaults published_date to current ISO timestamp when RSS entry date is missing."""
    mock_entry = MagicMock()
    mock_entry.title = "Test Article Without Date"
    mock_entry.summary = "Test summary"
    mock_entry.link = "https://example.com/test-article-no-date"
    # Ensure no date attributes
    del mock_entry.published_parsed
    del mock_entry.updated_parsed
    del mock_entry.published
    del mock_entry.updated

    mock_feed = MagicMock()
    mock_feed.bozo = False
    mock_feed.entries = [mock_entry]

    mock_resp = MagicMock()
    mock_resp.content = b"<rss></rss>"
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp), \
         patch("feedparser.parse", return_value=mock_feed):
        source = {"name": "Example Tech", "url": "https://example.com/rss", "type": "rss"}
        items = fetch_rss_feed(source)

        assert len(items) == 1
        item = items[0]
        assert item["title"] == "Test Article Without Date"
        assert item["source_name"] == "Example Tech"
        assert item["published_date"] is not None
        # Verify valid ISO date format
        assert "T" in item["published_date"]


def test_save_articles_published_at_fallback():
    """Test save_articles ensures published_at and source_name have non-empty defaults."""
    manager = SupabaseManager()
    manager.available = True
    mock_client = MagicMock()
    manager.client = mock_client

    mock_upsert = MagicMock()
    mock_upsert.execute.return_value.data = [{"id": "art-1"}]
    mock_client.table.return_value.upsert.return_value = mock_upsert

    articles = [
        {
            "title": "Article No Date",
            "summary": "Summary",
            "link": "https://example.com/1",
            "published_at": None,
            "source_name": "",
            "content_hash": "hash123"
        }
    ]

    manager.save_articles("run-1", "AI Models", articles)

    mock_client.table.assert_called_with("articles")
    upsert_args = mock_client.table.return_value.upsert.call_args[0][0]
    assert len(upsert_args) == 1
    row = upsert_args[0]
    assert row["source_name"] == "Unknown Source"
    assert row["published_at"] is not None
    assert "T" in row["published_at"]


def test_local_feedback_crud():
    """Test saving, retrieving, updating status, and deleting feedback in local JSON storage."""
    manager = SupabaseManager()
    manager.available = False  # Test local fallback mode

    # Save
    item = manager.save_feedback(
        category="feature",
        title="Test Feature Request",
        description="Feature description",
        status="open"
    )
    assert item is not None
    item_id = item["id"]
    assert item["title"] == "Test Feature Request"
    assert item["category"] == "feature"
    assert item["status"] == "open"

    # Get
    all_feedback = manager.get_all_feedback(category_filter="feature")
    assert any(i["id"] == item_id for i in all_feedback)

    # Update
    updated = manager.update_feedback_status(item_id, "in_progress")
    assert updated is not None
    assert updated["status"] == "in_progress"

    # Delete
    deleted = manager.delete_feedback(item_id)
    assert deleted is True
    remaining = manager.get_all_feedback()
    assert not any(i["id"] == item_id for i in remaining)
