"""
Unit tests for HTTP 429 / LLM Quota Exceeded fallback mechanisms.
Verifies that:
1. LLMClient detects 429 / usage limit responses, sets LLMQuotaExceededError, and aborts retries.
2. Evaluator halts LLM judges and executes deterministic judges only when quota is exceeded.

Note: the summariser no longer consults the quota flag — live-synthesis
fallback is owned by the ModelGateway (per-model health, ordered fallback
chain, deterministic last resort); see tests/test_routing_overhaul.py.
"""

from unittest.mock import MagicMock, patch
import pytest
import requests

from core import llm_client as llm_client_mod
from core.llm_client import LLMClient
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


# ---------------------------------------------------------------------------
# Cause tracking: 429 quota vs empty-response degradation
# ---------------------------------------------------------------------------

def test_get_degradation_cause_starts_none():
    assert LLMClient.get_degradation_cause() == "none"


def test_mark_quota_exceeded_sets_cause_429():
    LLMClient.mark_quota_exceeded("weekly usage limit")
    assert LLMClient.is_quota_exceeded() is True
    assert LLMClient.get_degradation_cause() == "quota_429"


def test_mark_degraded_empty_response_sets_cause_empty():
    LLMClient.mark_degraded_empty_response("3 empty 200 bodies")
    assert LLMClient.is_quota_exceeded() is True
    assert LLMClient.get_degradation_cause() == "empty_response"


def test_reset_quota_status_clears_cause():
    LLMClient.mark_degraded_empty_response("transient")
    LLMClient.reset_quota_status()
    assert LLMClient.is_quota_exceeded() is False
    assert LLMClient.get_degradation_cause() == "none"


def test_empty_response_recovery_probe_returns_true_on_200():
    """A 200 from /api/tags inside the retry loop means retry would succeed."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("requests.get", return_value=mock_resp):
        assert LLMClient._probe_recovery_available() is True


def test_empty_response_recovery_probe_returns_false_on_429():
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = "weekly limit"

    with patch("requests.get", return_value=mock_resp):
        assert LLMClient._probe_recovery_available() is False


def test_empty_response_recovery_probe_returns_false_on_network_error():
    with patch("requests.get", side_effect=requests.ConnectionError("dns")):
        assert LLMClient._probe_recovery_available() is False


def test_generate_recovers_on_empty_body_when_probe_returns_200():
    """If the first /api/generate returns an empty body but /api/tags is 200,
    a fresh retry should succeed without bumping the streak."""
    client = LLMClient()
    empty_resp = MagicMock(status_code=200)
    empty_resp.json.return_value = {"response": ""}
    empty_resp.text = ""
    good_resp = MagicMock(status_code=200)
    good_resp.json.return_value = {"response": "Recovered narrative."}
    good_resp.text = "Recovered narrative."

    probe_resp = MagicMock(status_code=200)

    with patch("requests.post", side_effect=[empty_resp, good_resp]) as mock_post, \
         patch("requests.get", return_value=probe_resp):
        result = client.generate("test prompt")

    assert result == "Recovered narrative."
    assert mock_post.call_count == 2  # one empty + one retry
    assert LLMClient.get_degradation_cause() == "none"
    assert LLMClient._get_empty_response_streak() == 0


def test_generate_marks_degraded_empty_response_after_threshold():
    """When /api/tags probe also fails across all 3 retries, the cause tag
    must be ``empty_response`` (not ``quota_429``) so the banner is honest.

    Exception classes are resolved through ``llm_client_mod`` at call time
    because ``test_evaluator.py`` runs ``importlib.reload(core.llm_client)``,
    which replaces the module's class objects in place; a name captured at
    import time would be the pre-reload class and never match the raised one.
    """
    client = llm_client_mod.LLMClient()
    empty_resp = MagicMock(status_code=200)
    empty_resp.json.return_value = {"response": ""}
    empty_resp.text = ""

    probe_fail = MagicMock(status_code=500)
    probe_fail.text = "service unavailable"

    with patch("requests.post", return_value=empty_resp), \
         patch("requests.get", return_value=probe_fail):
        with pytest.raises(llm_client_mod.LLMClientError) as exc_info:
            client.generate("test prompt")
        assert "empty response" in str(exc_info.value).lower()

    assert LLMClient.is_quota_exceeded() is True
    assert LLMClient.get_degradation_cause() == "empty_response"
    assert "Empty 200" in LLMClient.get_quota_message()


def test_quota_429_cause_unaffected_by_empty_response_path():
    """A real 429 must still tag the cause as quota_429, not empty_response.

    Class lookup goes through ``llm_client_mod`` for the same reload-safety
    reason as ``test_generate_marks_degraded_empty_response_after_threshold``.
    """
    client = llm_client_mod.LLMClient()
    quota_resp = MagicMock(status_code=429)
    quota_resp.text = '{"error":"weekly usage limit"}'

    with patch("requests.post", return_value=quota_resp):
        with pytest.raises(llm_client_mod.LLMQuotaExceededError):
            client.generate("test prompt")

    assert LLMClient.is_quota_exceeded() is True
    assert LLMClient.get_degradation_cause() == "quota_429"
