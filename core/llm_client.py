"""
LLM client wrapper for AI Pulse.
Provides a single reusable interface to the Ollama Cloud API with
exponential-backoff retries and structured error handling.
"""

import logging
import os
import threading
import time
from typing import Callable, Optional

import requests

from config.settings import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_API_KEY, OLLAMA_NUM_CTX

from core.logger import setup_logger

logger = setup_logger(__name__)

# Retry configuration
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 1.5

# Optional event-sink signature: (latency_ms: int, ok: bool, error_msg: str)
LLMEventSink = Callable[[int, bool, str], None]

# Debug toggle.  When the LLM_DEBUG env var is set to a truthy value
# ("1", "true", "yes"), the client dumps the prompt + system prompt +
# raw response body to the log on every empty or failed HTTP attempt.
# Default off because prompts may contain sensitive article content.
_LLM_DEBUG = os.environ.get("LLM_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
_DEBUG_PROMPT_CHARS = 500
_DEBUG_RESPONSE_CHARS = 1000


import sys

class LLMClientError(Exception):
    """Raised when the LLM API call fails after all retries."""


class LLMQuotaExceededError(LLMClientError):
    """Raised when the LLM API quota or rate limit is exhausted (HTTP 429 / usage limit)."""


_QUOTA_EXCEEDED_FLAG = "_aipulse_llm_quota_exceeded"
_QUOTA_MSG_FLAG = "_aipulse_llm_quota_message"


class LLMClient:
    """Thin wrapper around the Ollama Cloud /api/generate endpoint."""

    _api_lock = threading.Semaphore(3)

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        model: str = OLLAMA_MODEL,
        api_key: str = OLLAMA_API_KEY,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key

    # ------------------------------------------------------------------
    # Quota / Rate Limit Management
    # ------------------------------------------------------------------

    @classmethod
    def is_quota_exceeded(cls) -> bool:
        """Return True if the LLM API rate limit / weekly quota has been exceeded."""
        return getattr(sys, _QUOTA_EXCEEDED_FLAG, False)

    @classmethod
    def get_quota_message(cls) -> str:
        """Return details of the quota error message if present."""
        return getattr(sys, _QUOTA_MSG_FLAG, "")

    @classmethod
    def mark_quota_exceeded(cls, msg: str = "") -> None:
        """Mark LLM API quota as exceeded to stop further LLM calls across threads/session."""
        setattr(sys, _QUOTA_EXCEEDED_FLAG, True)
        if msg:
            setattr(sys, _QUOTA_MSG_FLAG, msg)
        logger.error("LLM Quota Exceeded flag set: %s", msg)

    @classmethod
    def reset_quota_status(cls) -> None:
        """Reset quota exceeded flag (e.g. for testing or manual recovery)."""
        setattr(sys, _QUOTA_EXCEEDED_FLAG, False)
        setattr(sys, _QUOTA_MSG_FLAG, "")

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if the Ollama Cloud endpoint is reachable and quota is active."""
        if self.is_quota_exceeded():
            return False
        try:
            headers = self._auth_headers()
            resp = requests.get(
                f"{self.base_url}/api/tags", headers=headers, timeout=10
            )
            return resp.status_code == 200
        except Exception:
            return False

    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1500,
        event_sink: Optional[LLMEventSink] = None,
    ) -> str:
        """Send a generation request with automatic retries.

        Returns the model's text response.
        Raises LLMQuotaExceededError if rate limit (HTTP 429) is hit.
        Raises LLMClientError if call fails or returns empty content.
        """
        if self.is_quota_exceeded():
            msg = self.get_quota_message() or "Ollama Cloud weekly usage limit reached (HTTP 429)."
            raise LLMQuotaExceededError(f"Quota exceeded: {msg}")

        payload = {
            "model": self.model,
            "prompt": (
                f"System: {system}\n\nUser: {prompt}" if system else prompt
            ),
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "num_ctx": OLLAMA_NUM_CTX,
            },
        }
        headers = self._auth_headers()

        last_error: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            if self.is_quota_exceeded():
                msg = self.get_quota_message() or "Ollama Cloud weekly usage limit reached (HTTP 429)."
                raise LLMQuotaExceededError(f"Quota exceeded: {msg}")

            if attempt == 2:
                payload["options"]["temperature"] = max(0.5, temperature)
                payload["options"]["num_predict"] = max_tokens + 100
            elif attempt == 3:
                payload["options"]["temperature"] = max(0.7, temperature + 0.3)
                payload["options"]["num_predict"] = max_tokens + 200
            else:
                payload["options"]["temperature"] = temperature
                payload["options"]["num_predict"] = max_tokens
            t0 = time.monotonic()
            try:
                with self._api_lock:
                    resp = requests.post(
                        f"{self.base_url}/api/generate",
                        json=payload,
                        headers=headers,
                        timeout=120,
                    )
                latency_ms = int((time.monotonic() - t0) * 1000)

                # Check for HTTP 429 or quota limit error strings
                is_429 = resp.status_code == 429
                is_quota_msg = any(kw in resp.text.lower() for kw in ["usage limit", "weekly limit", "quota", "upgrade for higher limits"])

                if is_429 or is_quota_msg:
                    err_text = resp.text[:300] if resp.text else f"HTTP {resp.status_code}"
                    self.mark_quota_exceeded(err_text)
                    last_error = LLMQuotaExceededError(f"HTTP 429 Quota Exceeded: {err_text}")
                    if event_sink is not None:
                        event_sink(latency_ms, False, str(last_error))
                    if _LLM_DEBUG:
                        _dump_failure(
                            label="quota_exceeded_429",
                            attempt=attempt,
                            latency_ms=latency_ms,
                            payload=payload,
                            resp=resp,
                            system=system,
                        )
                    # Instantly abort retries on quota exhaustion
                    raise last_error

                if resp.status_code == 200:
                    result = resp.json().get("response", "").strip()
                    if result:
                        if event_sink is not None:
                            event_sink(latency_ms, True, "")
                        return result
                    else:
                        last_error = LLMClientError("Ollama returned an empty response.")
                        if _LLM_DEBUG:
                            _dump_failure(
                                label="empty_response",
                                attempt=attempt,
                                latency_ms=latency_ms,
                                payload=payload,
                                resp=resp,
                                system=system,
                            )
                else:
                    last_error = LLMClientError(
                        f"HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                    if _LLM_DEBUG:
                        _dump_failure(
                            label="http_error",
                            attempt=attempt,
                            latency_ms=latency_ms,
                            payload=payload,
                            resp=resp,
                            system=system,
                        )

                if event_sink is not None:
                    event_sink(latency_ms, False, str(last_error))
                logger.warning(
                    "Ollama API issue [model=%s, attempt=%d/%d, latency=%dms, prompt_len=%d]: %s",
                    self.model,
                    attempt,
                    MAX_RETRIES,
                    latency_ms,
                    len(prompt),
                    last_error,
                )
            except requests.RequestException as exc:
                latency_ms = int((time.monotonic() - t0) * 1000)
                last_error = exc
                if event_sink is not None:
                    event_sink(latency_ms, False, str(exc))
                if _LLM_DEBUG:
                    logger.warning(
                        "LLM_DEBUG request_exception [attempt=%d/%d, latency=%dms, prompt_preview=%r]",
                        attempt,
                        MAX_RETRIES,
                        latency_ms,
                        prompt[:_DEBUG_PROMPT_CHARS],
                    )
                logger.warning(
                    "Ollama API request failed [model=%s, attempt=%d/%d, latency=%dms]: %s",
                    self.model,
                    attempt,
                    MAX_RETRIES,
                    latency_ms,
                    exc,
                )

            if attempt < MAX_RETRIES:
                backoff = INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
                time.sleep(backoff)

        logger.error(
            "Ollama API error: All %d attempts failed for model=%s [prompt_len=%d]. Last error: %s",
            MAX_RETRIES,
            self.model,
            len(prompt),
            last_error,
        )
        raise LLMClientError(
            f"All {MAX_RETRIES} attempts failed for model '{self.model}'. {last_error}"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict:
        headers: dict = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


def _dump_failure(
    *,
    label: str,
    attempt: int,
    latency_ms: int,
    payload: dict,
    resp: "requests.Response",
    system: Optional[str],
) -> None:
    """Log a structured dump of an LLM call failure (LLM_DEBUG mode).

    Sanitizes the Authorization header so the bearer token never lands in
    the log file.  Truncates the prompt and response body to keep the
    log readable.
    """
    prompt_text = payload.get("prompt", "") or ""
    response_text = ""
    try:
        response_text = resp.text or ""
    except Exception:  # noqa: BLE001
        response_text = "<unreadable>"

    # Sanitize headers
    safe_headers = {
        k: ("Bearer ***REDACTED***" if k.lower() == "authorization" else v)
        for k, v in (resp.request.headers if resp.request else {}).items()
    }

    logger.warning(
        "LLM_DEBUG [%s] attempt=%d latency_ms=%d url=%s status=%s "
        "content_type=%s request_headers=%s",
        label,
        attempt,
        latency_ms,
        resp.url,
        resp.status_code,
        resp.headers.get("content-type", "?"),
        safe_headers,
    )
    if system:
        logger.warning(
            "LLM_DEBUG [%s] system_prompt[:%d]=%r",
            label,
            _DEBUG_PROMPT_CHARS,
            system[:_DEBUG_PROMPT_CHARS],
        )
    logger.warning(
        "LLM_DEBUG [%s] payload_options=%s",
        label,
        payload.get("options"),
    )
    logger.warning(
        "LLM_DEBUG [%s] prompt[:%d]=%r",
        label,
        _DEBUG_PROMPT_CHARS,
        prompt_text[:_DEBUG_PROMPT_CHARS],
    )
    logger.warning(
        "LLM_DEBUG [%s] raw_response[:%d]=%r",
        label,
        _DEBUG_RESPONSE_CHARS,
        response_text[:_DEBUG_RESPONSE_CHARS],
    )
