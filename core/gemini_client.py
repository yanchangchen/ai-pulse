"""
Google Gemini API Client for AI Pulse.
Used exclusively for on-demand, per-theme deep dive summarisation.
Handles rate limits and quota exceeded errors with model fallback guidance.
"""

from __future__ import annotations

import logging
import time
import requests
from typing import Optional, Dict, Any, List

from config.settings import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_AVAILABLE_MODELS

logger = logging.getLogger(__name__)

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Transient server errors (500/502/503/504 — e.g. the "This model is currently
# experiencing high demand" overload spikes) are retried with backoff.
# 429 quota errors are NOT retried — they raise immediately so the UI can
# suggest switching models.
RETRYABLE_STATUS_CODES = (500, 502, 503, 504)
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (1.0, 2.0)  # sleep before attempt 2, then attempt 3


class GeminiClientError(Exception):
    """Base exception for Gemini API client errors."""
    pass


class GeminiQuotaError(GeminiClientError):
    """Raised when Gemini API quota or rate limit is reached."""
    def __init__(self, model: str, details: str = ""):
        self.model = model
        self.details = details
        suggested_models = [m for m in GEMINI_AVAILABLE_MODELS if m != model]
        suggestions_str = ", ".join(suggested_models[:3])
        message = (
            f"Google Gemini quota/rate limit reached (HTTP 429) for model '{model}'. "
            f"Please consider selecting another model ID (e.g. {suggestions_str}) "
            f"from the model selector."
        )
        super().__init__(message)


class GeminiClient:
    """Client for Google Gemini Generative Language API."""

    def __init__(self, api_key: Optional[str] = None, default_model: Optional[str] = None):
        self.api_key = api_key if api_key is not None else GEMINI_API_KEY
        self.default_model = default_model or GEMINI_MODEL or "gemini-3.7-flash"

    def is_configured(self) -> bool:
        """Check if an API key is present."""
        return bool(self.api_key and len(self.api_key.strip()) > 0)

    def generate_content(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_output_tokens: int = 4096,
        timeout: int = 30,
    ) -> str:
        """
        Generate text content using Google Gemini API.

        Args:
            prompt: User prompt text.
            system_instruction: Optional system instruction text.
            model: Model ID (e.g., 'gemini-3.7-flash').
            temperature: Sampling temperature.
            max_output_tokens: Max output tokens in response.
            timeout: Request timeout in seconds.

        Returns:
            Generated response string.

        Raises:
            GeminiQuotaError: When quota limit (HTTP 429) is reached.
            GeminiClientError: When any other API or network error persists
                after retrying transient 5xx responses (see MAX_ATTEMPTS).
        """
        target_model = model or self.default_model

        if not self.is_configured():
            raise GeminiClientError(
                "Google Gemini API key not configured. Please set GEMINI_API_KEY in "
                ".streamlit/secrets.toml or your environment variables."
            )

        url = f"{GEMINI_API_BASE_URL}/{target_model}:generateContent?key={self.api_key}"

        payload: Dict[str, Any] = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
            }
        }

        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        resp = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = requests.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=timeout
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                logger.warning(
                    "Gemini transient network error for '%s' (attempt %d/%d): %s",
                    target_model, attempt, MAX_ATTEMPTS, e,
                )
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF_SECONDS[attempt - 1])
                    continue
                raise GeminiClientError(
                    f"Gemini API request timed out/failed after {MAX_ATTEMPTS} attempts "
                    f"for model '{target_model}': {e}"
                )
            except requests.exceptions.RequestException as e:
                raise GeminiClientError(f"Gemini API network connection failed: {e}")

            # Check for rate limit / quota exceeded — surfaced immediately (never
            # retried) so the UI can suggest switching to another model.
            if resp.status_code == 429:
                error_data = {}
                try:
                    error_data = resp.json().get("error", {})
                except Exception:
                    pass
                error_msg = error_data.get("message", resp.text)
                logger.warning("Gemini quota exceeded for model '%s': %s", target_model, error_msg)
                raise GeminiQuotaError(model=target_model, details=error_msg)

            # Transient server errors (overload spikes, etc.) — retry with backoff.
            if resp.status_code in RETRYABLE_STATUS_CODES:
                try:
                    retry_msg = resp.json().get("error", {}).get("message", resp.text[:200])
                except Exception:
                    retry_msg = resp.text[:200]
                logger.warning(
                    "Gemini transient HTTP %s for '%s' (attempt %d/%d): %s",
                    resp.status_code, target_model, attempt, MAX_ATTEMPTS, retry_msg,
                )
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF_SECONDS[attempt - 1])
                    continue
                raise GeminiClientError(
                    f"Gemini API Error (HTTP {resp.status_code}) for '{target_model}' "
                    f"after {MAX_ATTEMPTS} attempts: {retry_msg}"
                )

            if resp.status_code != 200:
                error_msg = resp.text
                try:
                    error_json = resp.json()
                    if "error" in error_json:
                        error_msg = error_json["error"].get("message", resp.text)
                except Exception:
                    pass
                raise GeminiClientError(f"Gemini API Error (HTTP {resp.status_code}) for '{target_model}': {error_msg}")

            break  # HTTP 200 — proceed to response parsing

        try:
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise GeminiClientError(f"Gemini returned 0 candidates for model '{target_model}'.")

            parts = candidates[0].get("content", {}).get("parts", [])
            finish_reason = candidates[0].get("finishReason", "STOP")
            text_parts = [p.get("text", "") for p in parts if "text" in p]
            result_text = "".join(text_parts).strip()

            if not result_text:
                raise GeminiClientError(
                    f"Gemini returned empty text response "
                    f"(finish_reason={finish_reason}) for model '{target_model}'."
                )

            # Surface non-STOP finish reasons so future debugging doesn't have
            # to guess whether output was truncated or filtered.
            if finish_reason in ("MAX_TOKENS", "SAFETY", "RECITATION", "OTHER"):
                logger.warning(
                    "Gemini non-STOP finishReason for model '%s': %s (output length=%d)",
                    target_model, finish_reason, len(result_text),
                )

            return result_text

        except (KeyError, ValueError) as e:
            raise GeminiClientError(f"Failed to parse Gemini response: {e}")
