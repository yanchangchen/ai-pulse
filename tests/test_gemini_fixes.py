"""
Regression tests for the three Gemini fixes:
1. GeminiClient retries transient 5xx responses (never retries 429).
2. config.settings resolves keys nested under a secrets.toml section ([general]).
3. Gateway GeminiProvider builds generation configs even when the installed
   SDK lacks ThinkingConfig (deprecated google.generativeai).
"""

import pytest
from unittest.mock import patch, MagicMock

from core.gemini_client import GeminiClient, GeminiClientError, GeminiQuotaError


def _ok_response(text="OK"):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "candidates": [
            {"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}
        ]
    }
    return resp


def _error_response(status_code, message="error"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = message
    resp.json.return_value = {"error": {"message": message}}
    return resp


def _response_with_finish(text, finish_reason):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "candidates": [
            {"content": {"parts": [{"text": text}]}, "finishReason": finish_reason}
        ]
    }
    return resp


@patch("core.gemini_client.time.sleep")
@patch("requests.post")
def test_gemini_client_retries_transient_503_then_succeeds(mock_post, mock_sleep):
    mock_post.side_effect = [_error_response(503, "high demand"), _ok_response()]

    client = GeminiClient(api_key="test_key", default_model="gemini-3.5-flash-lite")
    result = client.generate_content("test prompt")

    assert result == "OK"
    assert mock_post.call_count == 2
    assert mock_sleep.call_count == 1


@patch("core.gemini_client.time.sleep")
@patch("requests.post")
def test_gemini_client_503_exhausts_retries_then_raises(mock_post, mock_sleep):
    mock_post.return_value = _error_response(503, "high demand")

    client = GeminiClient(api_key="test_key", default_model="gemini-3.5-flash-lite")
    with pytest.raises(GeminiClientError, match="after 3 attempts"):
        client.generate_content("test prompt")

    assert mock_post.call_count == 3
    assert mock_sleep.call_count == 2


@patch("core.gemini_client.time.sleep")
@patch("requests.post")
def test_gemini_client_429_never_retried(mock_post, mock_sleep):
    mock_post.return_value = _error_response(429, "quota exhausted")

    client = GeminiClient(api_key="test_key", default_model="gemini-3.5-flash-lite")
    with pytest.raises(GeminiQuotaError):
        client.generate_content("test prompt")

    assert mock_post.call_count == 1
    mock_sleep.assert_not_called()


def test_settings_find_in_mapping_nested_sections():
    from config.settings import _find_in_mapping

    nested = {"general": {"GEMINI_API_KEY": "abc123", "OLLAMA_MODEL": "m"}}
    assert _find_in_mapping(nested, "GEMINI_API_KEY") == "abc123"
    assert _find_in_mapping(nested, "OLLAMA_MODEL") == "m"

    flat = {"GEMINI_API_KEY": "top-level"}
    assert _find_in_mapping(flat, "GEMINI_API_KEY") == "top-level"

    assert _find_in_mapping({}, "MISSING") is None
    assert _find_in_mapping({"general": {}}, "MISSING") is None


def test_settings_resolves_keys_nested_under_general_section():
    """The real project secrets.toml nests keys under [general]; _get_secret
    must find them there (regression guard against top-level-only lookups)."""
    import toml
    from pathlib import Path
    from config.settings import _get_secret

    secrets_path = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"
    if not secrets_path.exists():
        pytest.skip("no local secrets.toml present")

    sections = toml.load(secrets_path)
    if "general" not in sections or "OLLAMA_BASE_URL" not in sections["general"]:
        pytest.skip("secrets.toml does not use the [general] layout")

    import os
    saved = os.environ.pop("OLLAMA_BASE_URL", None)
    try:
        assert _get_secret("OLLAMA_BASE_URL", "") == sections["general"]["OLLAMA_BASE_URL"]
    finally:
        if saved is not None:
            os.environ["OLLAMA_BASE_URL"] = saved


def test_gateway_provider_builds_config_without_thinking_support():
    """The deprecated google.generativeai SDK lacks ThinkingConfig; the
    provider must still build a valid GenerationConfig."""
    from core.ai_gateway.providers.gemini import GeminiProvider

    provider = GeminiProvider(api_key="test_key", model="gemini-3.5-flash-lite")
    config = provider._build_generation_config(temperature=0.2, max_output_tokens=64)

    assert config.temperature == 0.2
    assert config.max_output_tokens == 64


def test_gateway_provider_structured_config_includes_schema():
    from core.ai_gateway.providers.gemini import GeminiProvider

    provider = GeminiProvider(api_key="test_key", model="gemini-3.5-flash-lite")
    schema = {"type": "object", "properties": {"category": {"type": "string"}}}
    config = provider._build_generation_config(0.2, 512, schema=schema)

    assert config.response_mime_type == "application/json"
    assert config.response_schema == schema


# ---------------------------------------------------------------------------
# Deep Dive fixes: MAX_TOKENS budget escalation, thinking_level, upsert
# ---------------------------------------------------------------------------

@patch("core.gemini_client.time.sleep")
@patch("requests.post")
def test_gemini_client_escalates_budget_on_max_tokens(mock_post, mock_sleep):
    """A MAX_TOKENS-truncated response (Gemini 3 thinking ate the budget)
    triggers a retry with a doubled maxOutputTokens."""
    responses = [
        _response_with_finish("truncated...", "MAX_TOKENS"),
        _response_with_finish("complete brief", "STOP"),
    ]
    captured = []

    def capture(*args, **kwargs):
        import copy
        captured.append(copy.deepcopy(kwargs["json"]))
        return responses.pop(0)

    mock_post.side_effect = capture

    client = GeminiClient(api_key="test_key", default_model="gemini-3.5-flash")
    result = client.generate_content("write brief", max_output_tokens=2048)

    assert result == "complete brief"
    assert len(captured) == 2
    assert captured[0]["generationConfig"]["maxOutputTokens"] == 2048
    assert captured[1]["generationConfig"]["maxOutputTokens"] == 4096


@patch("core.gemini_client.time.sleep")
@patch("requests.post")
def test_gemini_client_returns_last_response_when_escalation_exhausts_attempts(mock_post, mock_sleep):
    mock_post.return_value = _response_with_finish("still truncated", "MAX_TOKENS")

    client = GeminiClient(api_key="test_key", default_model="gemini-3.5-flash")
    result = client.generate_content("write brief", max_output_tokens=2048)

    # No exception: the caller gets the best-effort (possibly truncated) text
    # and a logged warning, instead of losing the synthesis entirely.
    assert result == "still truncated"
    assert mock_post.call_count == 3  # MAX_ATTEMPTS


@patch("core.gemini_client.time.sleep")
@patch("requests.post")
def test_gemini_client_thinking_level_in_payload(mock_post, mock_sleep):
    mock_post.return_value = _response_with_finish("OK", "STOP")

    client = GeminiClient(api_key="test_key", default_model="gemini-3.5-flash")
    client.generate_content("hi", thinking_level="low")

    gen_cfg = mock_post.call_args.kwargs["json"]["generationConfig"]
    assert gen_cfg["thinkingConfig"] == {"thinkingLevel": "low"}


@patch("core.gemini_client.time.sleep")
@patch("requests.post")
def test_gemini_client_no_thinking_config_by_default(mock_post, mock_sleep):
    mock_post.return_value = _response_with_finish("OK", "STOP")

    client = GeminiClient(api_key="test_key", default_model="gemini-3.5-flash")
    client.generate_content("hi")

    gen_cfg = mock_post.call_args.kwargs["json"]["generationConfig"]
    assert "thinkingConfig" not in gen_cfg


def test_save_theme_summary_upserts_on_run_theme_conflict():
    """On-demand re-synthesis for the same run must UPSERT — a plain insert
    hit 409 duplicate key (theme_summaries_run_id_theme_name_key)."""
    from core.supabase_client import SupabaseManager

    manager = SupabaseManager()
    manager.available = True
    manager.client = MagicMock()

    exec_result = MagicMock()
    exec_result.data = [{"id": 1, "theme_name": "Enterprise Strategy & ROI"}]
    table_mock = manager.client.table.return_value
    table_mock.upsert.return_value.execute.return_value = exec_result

    summary = {
        "what_is_happening": "hi", "engineering_tradeoffs": "e",
        "product_impact": "p", "why_it_matters": "w", "what_to_watch": "t",
        "further_reading": "", "_source": "gemini:gemini-3.5-flash",
        "_generation_log": {"model": "gemini-3.5-flash"},
    }
    result = manager.save_theme_summary(
        "run-1", "Enterprise Strategy & ROI", summary, article_count=5
    )

    assert result is exec_result.data[0]
    table_mock.upsert.assert_called_once()
    call = table_mock.upsert.call_args
    assert call.kwargs["on_conflict"] == "run_id,theme_name"
    assert call.args[0]["run_id"] == "run-1"
    assert call.args[0]["generation_source"] == "gemini:gemini-3.5-flash"
    # No plain insert anywhere on the persistence path
    table_mock.insert.assert_not_called()
