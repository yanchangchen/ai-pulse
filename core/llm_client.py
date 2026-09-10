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

from config.settings import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_API_KEY, get_ollama_num_ctx

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


# Two distinct causes the LLM client may be unusable:
#   * quota_429     — Ollama returned HTTP 429 (weekly usage limit)
#   * empty_response — Ollama returned 200 with an empty body, repeatedly
# Both states prevent live synthesis, but they mean different things to the
# user.  Downstream UI consults ``get_degradation_cause()`` to render an
# honest banner; ``is_quota_exceeded()`` continues to return True for BOTH
# so existing fallback paths (summariser, evaluator, Sage, sidebar) keep
# skipping live LLM calls.
_DEGRADATION_CAUSE_NONE = "none"
_DEGRADATION_CAUSE_QUOTA = "quota_429"
_DEGRADATION_CAUSE_EMPTY = "empty_response"

_QUOTA_EXCEEDED_FLAG = "_aipulse_llm_quota_exceeded"
_QUOTA_MSG_FLAG = "_aipulse_llm_quota_message"
_QUOTA_TIME_FLAG = "_aipulse_llm_quota_time"
_QUOTA_CAUSE_FLAG = "_aipulse_llm_quota_cause"

# Process-local counter of consecutive empty-response failures across all
# client instances.  Incremented on each empty 200, reset on a successful
# generate().  Used by core.summariser to detect a degraded LLM path
# (e.g. an upstream model returning empty bodies) and switch the rest of
# the run to the non-LLM extractive fallback before wasting more time
# on retries.
_EMPTY_FAIL_COUNTER = "_aipulse_llm_empty_fail_streak"


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
        """Return True if the LLM API is currently unusable.

        "Quota exceeded" is the legacy name; in practice the flag covers two
        distinct causes tracked separately via :func:`get_degradation_cause`:

        * ``quota_429``     — Ollama returned HTTP 429 (weekly usage limit)
        * ``empty_response`` — Ollama returned 200 with an empty body repeatedly

        Both states mean "skip live LLM, fall back to extractive" so the
        summariser / evaluator / Sage all consult this single boolean.
        """
        flag = getattr(sys, _QUOTA_EXCEEDED_FLAG, False)
        if flag:
            # Auto-reset quota flag after 1 hour (3600 seconds) so system automatically retries
            quota_time = getattr(sys, _QUOTA_TIME_FLAG, 0)
            if time.time() - quota_time > 3600:
                cls.reset_quota_status()
                return False
        return flag

    @classmethod
    def get_degradation_cause(cls) -> str:
        """Return the underlying reason live LLM synthesis is paused.

        One of:
            * ``"none"``            — LLM is healthy, no degradation in effect
            * ``"quota_429"``       — Ollama returned HTTP 429 (weekly usage limit)
            * ``"empty_response"``  — Ollama repeatedly returned 200 with empty body

        The cause survives :func:`reset_quota_status` only when explicitly
        re-marked.  This lets the sidebar render an honest banner ("quota
        limit reached (HTTP 429)" vs "returning empty responses") instead of
        always blaming quota.
        """
        return getattr(sys, _QUOTA_CAUSE_FLAG, _DEGRADATION_CAUSE_NONE)

    @classmethod
    def get_quota_message(cls) -> str:
        """Return details of the quota error message if present."""
        return getattr(sys, _QUOTA_MSG_FLAG, "")

    @classmethod
    def mark_quota_exceeded(cls, msg: str = "") -> None:
        """Mark the LLM as unusable due to a real 429 / quota response.

        Use this for genuine HTTP 429 / weekly-limit responses.  For
        empty-body degradation, prefer :func:`mark_degraded_empty_response`
        so the UI can distinguish the cause.
        """
        cls._mark_degraded(_DEGRADATION_CAUSE_QUOTA, msg)

    @classmethod
    def mark_degraded_empty_response(cls, msg: str = "") -> None:
        """Mark the LLM as unusable because it keeps returning empty bodies.

        Distinct from :func:`mark_quota_exceeded` so the UI can show an
        honest "live Ollama LLM returning empty responses" banner rather
        than blaming quota.  ``is_quota_exceeded()`` still returns True so
        the summariser, evaluator and Sage all skip live synthesis.
        """
        cls._mark_degraded(_DEGRADATION_CAUSE_EMPTY, msg)

    @classmethod
    def _mark_degraded(cls, cause: str, msg: str) -> None:
        """Internal: set the unified "LLM is paused" flag with a cause tag."""
        setattr(sys, _QUOTA_EXCEEDED_FLAG, True)
        setattr(sys, _QUOTA_TIME_FLAG, time.time())
        setattr(sys, _QUOTA_CAUSE_FLAG, cause)
        if msg:
            setattr(sys, _QUOTA_MSG_FLAG, msg)
        if cause == _DEGRADATION_CAUSE_QUOTA:
            logger.error("LLM Quota Exceeded flag set: %s", msg)
        else:
            logger.error("LLM degraded (%s) flag set: %s", cause, msg)

    @classmethod
    def reset_quota_status(cls) -> None:
        """Reset quota exceeded flag (e.g. for testing, manual refresh, or pipeline restart)."""
        setattr(sys, _QUOTA_EXCEEDED_FLAG, False)
        setattr(sys, _QUOTA_MSG_FLAG, "")
        setattr(sys, _QUOTA_TIME_FLAG, 0)
        setattr(sys, _QUOTA_CAUSE_FLAG, _DEGRADATION_CAUSE_NONE)
        setattr(sys, _EMPTY_FAIL_COUNTER, 0)
        logger.info("LLM Quota Exceeded status reset.")

    @classmethod
    def probe_quota_status(cls) -> bool:
        """Active health probe against the Ollama /api/tags endpoint.

        Intended to be called before triggering a refresh, so the user does
        not have to wait out the 1-hour auto-cooldown if Ollama quota has
        already been refreshed upstream.

        Logic:
            * 200 from /api/tags    → quota is back; call reset_quota_status() and return True
            * 429 / quota keyword   → quota still exceeded; refresh the timestamp + message
                                      so is_quota_exceeded() keeps returning True
            * transport / other err → cannot confirm; leave the flag as-is and return False

        Returns True if the LLM is confirmed available, False otherwise.
        """
        try:
            client = cls()
            headers = client._auth_headers()
            resp = requests.get(
                f"{client.base_url}/api/tags",
                headers=headers,
                timeout=10,
            )
        except Exception as exc:
            logger.warning(
                "LLM quota probe could not reach %s: %s",
                getattr(cls(), "base_url", "?"), exc,
            )
            return False

        if resp.status_code == 200:
            if getattr(sys, _QUOTA_EXCEEDED_FLAG, False):
                logger.info(
                    "LLM quota probe: /api/tags returned 200 — quota is back, "
                    "resetting quota-exceeded flag."
                )
            cls.reset_quota_status()
            return True

        # Quota still exhausted — refresh the timestamp so the 1-hour cooldown
        # window restarts from this probe rather than from the original failure.
        is_quota_response = (
            resp.status_code == 429
            or any(
                kw in resp.text.lower()
                for kw in ["usage limit", "weekly limit", "quota", "upgrade for higher limits"]
            )
        )
        if is_quota_response:
            cls.mark_quota_exceeded(resp.text[:300] if resp.text else f"HTTP {resp.status_code}")
            return False

        # Some other HTTP status (5xx, auth, etc.) — leave the flag alone.
        logger.warning(
            "LLM quota probe: unexpected status %s — leaving quota flag unchanged.",
            resp.status_code,
        )
        return False

    # ------------------------------------------------------------------
    # Empty-response degradation tracking
    # ------------------------------------------------------------------

    @classmethod
    def _record_empty_response(cls) -> int:
        """Increment the consecutive-empty-response counter and return the new value."""
        current = getattr(sys, _EMPTY_FAIL_COUNTER, 0)
        current += 1
        setattr(sys, _EMPTY_FAIL_COUNTER, current)
        return current

    @classmethod
    def _reset_empty_response_counter(cls) -> None:
        """Clear the consecutive-empty-response counter on a successful generate()."""
        setattr(sys, _EMPTY_FAIL_COUNTER, 0)

    @classmethod
    def _get_empty_response_streak(cls) -> int:
        """Return the current consecutive-empty-response failure count."""
        return getattr(sys, _EMPTY_FAIL_COUNTER, 0)

    @classmethod
    def _probe_recovery_available(cls) -> bool:
        """Single-shot /api/tags probe to see if a fresh request would succeed.

        Called from inside :func:`generate` after an empty-200 response.  A
        200 here means the upstream endpoint is healthy enough to retry
        the original prompt — so we should NOT bump the per-run streak.
        Anything else (429 / 5xx / network) means the LLM is genuinely
        degraded for now and the streak should advance.
        """
        try:
            client = cls()
            headers = client._auth_headers()
            resp = requests.get(
                f"{client.base_url}/api/tags",
                headers=headers,
                timeout=10,
            )
        except Exception as exc:
            logger.warning(
                "Empty-response recovery probe failed (%s); counting toward streak.",
                exc,
            )
            return False
        if resp.status_code == 200:
            return True
        # 429 or anything else: model-side is the bottleneck, count it.
        return False

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if the Ollama Cloud endpoint is reachable and quota is active.

        A 200 response from /api/tags is proof-of-life that the upstream quota
        has been refreshed — even if our local flag was left over from a prior
        run, a healthy 200 wins.  We self-heal by calling ``reset_quota_status()``
        so every downstream ``is_quota_exceeded()`` consult sees the fresh state
        for the rest of the session.  This is intentionally safer than waiting
        for the 1-hour cooldown, because the user-visible signal (Fetch &
        Refresh, weekly quota reset) is best-effort and the user has no other
        way to push the system back to Ollama other than a process restart.
        """
        if self.is_quota_exceeded():
            return False
        try:
            headers = self._auth_headers()
            resp = requests.get(
                f"{self.base_url}/api/tags", headers=headers, timeout=10
            )
        except Exception:
            return False
        if resp.status_code != 200:
            return False
        # 200 proves the API is reachable AND quota is active.  If a stale
        # flag somehow survived, clear it now so subsequent is_quota_exceeded()
        # consults (Sage, evaluator, summariser, sidebar) all see fresh state.
        # Only reset when the flag is actually set, so the per-run empty-
        # response counter is never clobbered by an unrelated health probe.
        if getattr(sys, _QUOTA_EXCEEDED_FLAG, False):
            LLMClient.reset_quota_status()
        return True

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
                "num_ctx": get_ollama_num_ctx(self.model),
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
                        LLMClient._reset_empty_response_counter()
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
                        # Self-heal: an empty 200 often means the upstream
                        # model is briefly wedged, not that the model is
                        # gone.  Hit /api/tags once to see if a fresh
                        # request would succeed.  Only on probe-fail do we
                        # count this toward the per-run degradation streak
                        # and (at the threshold) flip the degraded flag.
                        if LLMClient._probe_recovery_available():
                            logger.info(
                                "Empty 200 on attempt %d/%d for model=%s — "
                                "/api/tags probe returned 200; will retry the "
                                "original prompt without bumping streak.",
                                attempt, MAX_RETRIES, self.model,
                            )
                        else:
                            LLMClient._record_empty_response()
                            if attempt == MAX_RETRIES:
                                LLMClient.mark_degraded_empty_response(
                                    f"Empty 200 on all {MAX_RETRIES} attempts; "
                                    f"/api/tags probe did not confirm recovery."
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
