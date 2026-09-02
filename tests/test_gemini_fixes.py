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
