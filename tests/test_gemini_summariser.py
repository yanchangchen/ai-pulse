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
    assert "Claude 3.7 Release" in summary["further_reading"]


def test_parse_summary_sections_preserves_structure():
    """Verify the parser preserves bullet lists, paragraph breaks, and all 5 sections."""
    from core.summariser import _parse_summary_sections

    raw = """## 1. WHAT IS HAPPENING

**Google** released Gemini 2.5 Pro with native multimodal reasoning. The model achieves state-of-the-art on MMLU and HumanEval benchmarks. **OpenAI** countered with o3-mini optimized for cost-sensitive deployments. This signals a clear bifurcation: frontier labs now compete on cost-efficiency, not just capability.

## 2. ENGINEERING TRADEOFFS & BLUEPRINT

Engineers face a classic latency-vs-accuracy tradeoff with the new reasoning models. Gemini 2.5 Pro's extended thinking adds 2–8 seconds of latency but lifts code generation accuracy by 15%. The API now supports streaming partial thoughts, enabling UI teams to show progress indicators. The core tradeoff is clear: pay the latency cost for complex tasks, or route to lighter models for simple queries.

## 3. PRODUCT IMPACT & FEASIBILITY

Product teams can now unify their model stack — one provider for both cheap chat and deep analysis. **Google**'s aggressive pricing ($1.25/1M input tokens) undercuts **OpenAI** by 40% for equivalent capability tiers. Compliance-wise, Gemini 2.5 supports data residency in the EU, unlocking regulated verticals. Production-ready for enterprise with the caveat that extended thinking latency needs UX mitigation.

## 4. ACTIONABLE WATCHLIST
- **Gemini 2.5 Pro API GA** — Expected full GA in Q3 2025; lock in preview pricing now.
- **o3-mini cost benchmarks** — Independent cost-per-task evaluations due from Artificial Analysis by June.
- **EU AI Act compliance** — Final technical standards publish in August; review model card requirements.

## 5. STRATEGIC FURTHER READING
- **Gemini 2.5 Pro Technical Report** | Google DeepMind | https://deepmind.google/gemini-2-5
  *Why read this:* Detailed architecture changes and benchmark methodology for the new reasoning mode.
- **o3-mini Pricing Analysis** | The Information | https://theinformation.com/o3-mini-pricing
  *Why read this:* First independent cost-per-task comparison across frontier model providers.
"""

    parsed = _parse_summary_sections(raw)

    # Section 1: full prose preserved with paragraph structure
    assert "Google" in parsed["what_is_happening"]
    assert "bifurcation" in parsed["what_is_happening"]

    # Section 2: engineering content present
    assert "latency-vs-accuracy" in parsed["engineering_tradeoffs"]
    assert "streaming partial thoughts" in parsed["engineering_tradeoffs"]

    # Section 3: product content present
    assert "$1.25/1M input tokens" in parsed["product_impact"]
    assert "data residency" in parsed["product_impact"]

    # Section 4: bullet list preserved with newlines
    assert "Gemini 2.5 Pro API GA" in parsed["what_to_watch"]
    assert "o3-mini cost benchmarks" in parsed["what_to_watch"]
    assert "EU AI Act compliance" in parsed["what_to_watch"]
    # Bullets should be separate lines, not collapsed
    assert "\n" in parsed["what_to_watch"]

    # Section 5: further reading present and structured
    assert "Gemini 2.5 Pro Technical Report" in parsed["further_reading"]
    assert "o3-mini Pricing Analysis" in parsed["further_reading"]
    assert "deepmind.google" in parsed["further_reading"]
    assert "\n" in parsed["further_reading"]

    # why_it_matters should compose from engineering + product when no WHY IT MATTERS section
    assert "Engineering Blueprint" in parsed["why_it_matters"]
    assert "Product Feasibility" in parsed["why_it_matters"]
