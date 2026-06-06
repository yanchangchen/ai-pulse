"""
LLM client wrapper for AI Pulse.
Provides a single reusable interface to the Ollama Cloud API with
exponential-backoff retries and structured error handling.
"""

import logging
import time
from typing import Optional

import requests

from config.settings import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_API_KEY

from core.logger import setup_logger

logger = setup_logger(__name__)

# Retry configuration
MAX_RETRIES = 2
INITIAL_BACKOFF_SECONDS = 2.0


class LLMClientError(Exception):
    """Raised when the LLM API call fails after all retries."""


class LLMClient:
    """Thin wrapper around the Ollama Cloud /api/generate endpoint."""

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
    # Public helpers
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if the Ollama Cloud endpoint is reachable."""
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
    ) -> str:
        """Send a generation request with automatic retries.

        Returns the model's text response.
        Raises LLMClientError if call fails or returns empty content.
        """
        payload = {
            "model": self.model,
            "prompt": (
                f"System: {system}\n\nUser: {prompt}" if system else prompt
            ),
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        headers = self._auth_headers()

        last_error: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = requests.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                    headers=headers,
                    timeout=120,
                )
                if resp.status_code == 200:
                    result = resp.json().get("response", "").strip()
                    if result:
                        return result
                    else:
                        last_error = LLMClientError("Ollama returned an empty response.")
                else:
                    last_error = LLMClientError(
                        f"HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                
                logger.warning(
                    "Ollama API issue: %s (attempt %d/%d)",
                    last_error,
                    attempt,
                    MAX_RETRIES,
                )
            except requests.RequestException as exc:
                last_error = exc
                logger.warning(
                    "Ollama request failed (attempt %d/%d): %s",
                    attempt,
                    MAX_RETRIES,
                    exc,
                )

            if attempt < MAX_RETRIES:
                backoff = INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
                time.sleep(backoff)

        raise LLMClientError(
            f"All {MAX_RETRIES} attempts failed. {last_error}"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict:
        headers: dict = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
