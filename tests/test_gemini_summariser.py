"""
Unit tests for Google Gemini API Client and On-Demand Theme Summariser.
"""

import pytest
from unittest.mock import patch, MagicMock

from core.gemini_client import GeminiClient, GeminiClientError, GeminiQuotaError
from core.summariser import generate_gemini_theme_summary


def test_gemini_client_configuration():
    client = GeminiClient(api_key="test_key", default_model="gemini-3.7-flash")
    assert client.is_configured() is True
    assert client.default_model == "gemini-3.7-flash"

    unconfigured = GeminiClient(api_key="", default_model="gemini-3.7-flash")
    assert unconfigured.is_configured() is False
    with pytest.raises(GeminiClientError, match="not configured"):
        unconfigured.generate_content("test prompt")


@patch("requests.post")
def test_gemini_client_successful_generation(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "Synthetic test brief from Gemini 3.7 Flash."}]
                }
            }
        ]
    }
    mock_post.return_value = mock_resp

    client = GeminiClient(api_key="valid_test_key", default_model="gemini-3.7-flash")
    res = client.generate_content("Analyze AI developments")
    assert res == "Synthetic test brief from Gemini 3.7 Flash."


@patch("requests.post")
def test_gemini_client_quota_exceeded_error_guidance(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.json.return_value = {
        "error": {
            "code": 429,
            "message": "Resource has been exhausted (e.g. check quota)."
        }
    }
    mock_post.return_value = mock_resp

    client = GeminiClient(api_key="valid_test_key", default_model="gemini-3.7-flash")
    with pytest.raises(GeminiQuotaError) as exc_info:
        client.generate_content("Analyze AI developments", model="gemini-3.7-flash")

    err_msg = str(exc_info.value)
    assert "quota/rate limit reached" in err_msg
    assert "gemini-3.7-flash" in err_msg
    assert "Please consider selecting another model ID" in err_msg


@patch("core.summariser.GeminiClient")
def test_generate_gemini_theme_summary_structure(mock_gemini_cls):
    mock_instance = MagicMock()
    mock_instance.default_model = "gemini-3.7-flash"
    mock_instance.generate_content.return_value = """## 1. WHAT IS HAPPENING
Anthropic launched Claude 3.7 Sonnet with hybrid reasoning capabilities.

## 2. ENGINEERING TRADEOFFS & BLUEPRINT
Engineers can dynamically allocate thinking tokens between 0 and 128k depending on latency tolerance.

## 3. PRODUCT IMPACT & FEASIBILITY
Product teams can ship unified models for both low-latency chat and deep reasoning tasks.

## 4. ACTIONABLE WATCHLIST
- **API Token Limits** — Check SDK updates for reasoning budgets.

## 5. STRATEGIC FURTHER READING
- **Claude 3.7 Release** | Anthropic | https://anthropic.com/claude-3-7
"""
    mock_gemini_cls.return_value = mock_instance

    articles = [
        {"title": "Claude 3.7 Sonnet Launched", "summary": "Hybrid reasoning model.", "source_name": "Anthropic"}
    ]

    summary = generate_gemini_theme_summary("Frontier Models & Benchmarks", articles, model="gemini-3.7-flash")
    assert "Claude 3.7" in summary["what_is_happening"]
    assert "Gemini gemini-3.7-flash Synthesized" in summary["what_is_happening"]
    assert "thinking tokens" in summary["engineering_tradeoffs"]
    assert "Product teams" in summary["product_impact"]
    assert "API Token Limits" in summary["what_to_watch"]
