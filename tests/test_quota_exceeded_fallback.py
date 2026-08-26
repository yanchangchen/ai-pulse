"""
Unit tests for HTTP 429 / LLM Quota Exceeded fallback mechanisms.
Verifies that:
1. LLMClient detects 429 / usage limit responses, sets LLMQuotaExceededError, and aborts retries.
2. Summariser falls back to cached summaries when quota is exceeded.
3. Evaluator halts LLM judges and executes deterministic judges only when quota is exceeded.
"""

from unittest.mock import MagicMock, patch
import pytest
import requests

from core.llm_client import LLMClient, LLMClientError, LLMQuotaExceededError
from core.summariser import generate_all_summaries
from core.evaluator import _execute_judges_and_build_report


def setup_function():
    LLMClient.reset_quota_status()


def teardown_function():
    LLMClient.reset_quota_status()


def test_llm_client_quota_detection_429():
    client = LLMClient()
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = '{"error":"you have reached your weekly usage limit"}'

    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(Exception) as exc_info:
            client.generate("Test prompt")
        assert "429" in str(exc_info.value) or "weekly usage limit" in str(exc_info.value)

    assert LLMClient.is_quota_exceeded() is True
    assert "weekly usage limit" in LLMClient.get_quota_message()


def test_llm_client_fast_abort_when_quota_flag_active():
    LLMClient.mark_quota_exceeded("Weekly quota exceeded test")
    client = LLMClient()

    with patch("requests.post") as mock_post:
        with pytest.raises(Exception) as exc_info:
            client.generate("Test prompt")
        assert "Quota exceeded" in str(exc_info.value)

        # Must not perform any HTTP requests when quota flag is active
        mock_post.assert_not_called()


def test_summariser_fallback_to_extractive_on_quota():
    LLMClient.mark_quota_exceeded("Quota test")

    themed_articles = {"Agentic Systems & DevTools": [{"title": "Art 1", "summary": "Extractive sentence summary."}]}
    full_articles = [{"title": "Art 1"}]

    with patch("core.summariser.save_run_to_history"):
        summaries = generate_all_summaries(themed_articles, full_articles)

    assert "Agentic Systems & DevTools" in summaries
    assert "⚡" in summaries["Agentic Systems & DevTools"]["what_is_happening"]
    assert "Non-LLM Extractive Summary" in summaries["Agentic Systems & DevTools"]["what_is_happening"]


def test_evaluator_runs_deterministic_only_on_quota():
    LLMClient.mark_quota_exceeded("Quota limit test")

    runs = [{"id": "run-1", "run_timestamp": "2026-08-07T00:00:00Z", "run_date": "2026-08-07"}]
    articles_by_run = {"run-1": [{"title": "Art 1", "theme_name": "Agentic Systems & DevTools"}]}
    summaries_by_run = {"run-1": {"Agentic Systems & DevTools": {"what_is_happening": "Test summary"}}}
    prior_summaries_by_run = {}

    report = _execute_judges_and_build_report(
        runs=runs,
        articles_by_run=articles_by_run,
        summaries_by_run=summaries_by_run,
        prior_summaries_by_run=prior_summaries_by_run,
        threshold=0.8,
        lookback_days=7,
        judge_selection="all",
    )

    # LLM judges should be marked as skipped
    raw_m = report.raw_metrics
    assert raw_m.get("categoriser", {}).get("skipped") is True
    assert raw_m.get("faithfulness", {}).get("skipped") is True
    assert raw_m.get("uniqueness", {}).get("skipped") is True

    # Deterministic judges should have run and computed scores
    assert report.grounding_score >= 0.0
    assert report.structural_compliance_score >= 0.0
    assert report.coverage_score >= 0.0
    assert report.temporal_coherence_score >= 0.0


def test_format_display_timestamp():
    from core.design_system import format_display_timestamp

    assert format_display_timestamp("2026-08-11T11:04:58+00:00") == "11/08/2026 11:04:58"
    assert format_display_timestamp("2026-08-11 11:04:58") == "11/08/2026 11:04:58"
    assert format_display_timestamp("2026-08-11T11:04:58Z") == "11/08/2026 11:04:58"
    assert format_display_timestamp("") == ""
    assert format_display_timestamp(None) == ""


def test_probe_resets_flag_on_200():
    """A 200 from /api/tags proves quota is back — the flag must clear."""
    LLMClient.mark_quota_exceeded("Pre-probe stale flag")
    assert LLMClient.is_quota_exceeded() is True

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("requests.get", return_value=mock_resp) as mock_get:
        result = LLMClient.probe_quota_status()

    assert result is True
    assert LLMClient.is_quota_exceeded() is False
    assert LLMClient.get_quota_message() == ""
    # Probe must hit /api/tags (not /api/generate)
    called_url = mock_get.call_args[0][0]
    assert called_url.endswith("/api/tags")


def test_probe_refreshes_flag_on_429():
    """A 429 from /api/tags means quota is still exhausted — keep flag set."""
    LLMClient.reset_quota_status()
    assert LLMClient.is_quota_exceeded() is False

    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = '{"error":"weekly usage limit"}'

    with patch("requests.get", return_value=mock_resp):
        result = LLMClient.probe_quota_status()

    assert result is False
    assert LLMClient.is_quota_exceeded() is True
    assert "weekly usage limit" in LLMClient.get_quota_message()


def test_probe_leaves_flag_unchanged_on_transport_error():
    """If we cannot reach Ollama, we cannot confirm — leave the flag as-is."""
    LLMClient.mark_quota_exceeded("Pre-probe flag")
    assert LLMClient.is_quota_exceeded() is True
    msg_before = LLMClient.get_quota_message()

    with patch("requests.get", side_effect=requests.ConnectionError("dns down")):
        result = LLMClient.probe_quota_status()

    assert result is False
    # Flag is preserved — we don't know whether quota is back.
    assert LLMClient.is_quota_exceeded() is True
    assert LLMClient.get_quota_message() == msg_before


def test_probe_leaves_flag_unchanged_on_unexpected_status():
    """A 5xx / auth error is ambiguous — preserve the flag, do not mark fresh."""
    LLMClient.mark_quota_exceeded("Pre-probe flag")
    assert LLMClient.is_quota_exceeded() is True

    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.text = "service unavailable"

    with patch("requests.get", return_value=mock_resp):
        result = LLMClient.probe_quota_status()

    assert result is False
    # Flag preserved — original error message intact, not overwritten.
    assert LLMClient.is_quota_exceeded() is True
    assert LLMClient.get_quota_message() == "Pre-probe flag"
