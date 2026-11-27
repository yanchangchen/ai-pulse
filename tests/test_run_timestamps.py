"""Run timestamp timezone correctness.

Regression for the UTC+8 headless run that landed 8h "in the future":
- _save_to_supabase must send an aware UTC timestamp, never the naive local
  string (Postgres reads naive values as UTC).
- is_duplicate_run must parse ISO-8601 cloud timestamps (the old strptime
  with the local-key format silently failed on them).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import core.history_manager as hm


def test_save_to_supabase_sends_aware_utc():
    sb = MagicMock()
    sb.is_available.return_value = True
    sb.save_trend_run.return_value = {"id": "run-1"}
    local_ts = "2026-11-27 23:27:19"
    with patch("core.supabase_client.get_supabase_manager", return_value=sb):
        hm._save_to_supabase(local_ts, "2026-11-27", {}, {}, [], {})
    sent = sb.save_trend_run.call_args.kwargs["run_timestamp"]
    parsed = datetime.fromisoformat(sent)
    assert parsed.tzinfo is not None, "must be timezone-aware"
    assert sent.endswith("+00:00")
    expected = datetime.strptime(local_ts, "%Y-%m-%d %H:%M:%S").astimezone(timezone.utc)
    assert parsed == expected


def test_is_duplicate_run_parses_iso_cloud_timestamp():
    sb = MagicMock()
    sb.is_available.return_value = True
    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    sb.get_latest_run.return_value = {
        "article_fingerprint": "fp-123",
        "run_timestamp": recent,
    }
    with patch("core.supabase_client.get_supabase_manager", return_value=sb):
        with patch.object(hm, "_latest_local_run_timestamp_and_fingerprint", return_value=(None, None)):
            assert hm.is_duplicate_run("fp-123") is True
            assert hm.is_duplicate_run("other-fp") is False


def test_is_duplicate_run_old_cloud_run_not_duplicate():
    sb = MagicMock()
    sb.is_available.return_value = True
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    sb.get_latest_run.return_value = {
        "article_fingerprint": "fp-123",
        "run_timestamp": old,
    }
    with patch("core.supabase_client.get_supabase_manager", return_value=sb):
        with patch.object(hm, "_latest_local_run_timestamp_and_fingerprint", return_value=(None, None)):
            assert hm.is_duplicate_run("fp-123") is False
