"""
Shared pytest fixtures for AI Pulse.

This file is auto-discovered by pytest and provides:

- ``integration`` marker registration (opt-in via ``-m integration``)
- ``llm_table`` session-scoped fixture: a callable that returns a
  canned-response dispatcher plus a recording of every LLM call.
- ``clean_judge_events`` module-scope fixture: drains the judge-event
  ring buffer between tests so events from one test never leak into
  another.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Tuple

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers.  ``integration`` is opt-in."""
    config.addinivalue_line(
        "markers",
        "integration: marks tests that exercise the real LLM and Supabase "
        "wiring.  Opt-in with `pytest -m integration`.",
    )


# ---------------------------------------------------------------------------
# Canned LLM fixture
# ---------------------------------------------------------------------------


class CannedLLM:
    """A tiny in-memory fake of LLMClient.

    Tests build it via the ``llm_table`` fixture and either:

    - register ``(prompt_substring -> response_text)`` rules with
      ``.when(contains=, returns=)``; the first matching rule wins
      (case-insensitive substring match)
    - or call ``.set_default(response)`` so every prompt returns the
      same canned body (useful for "every classification is correct" /
      "every overlap is 0.5" smoke tests)

    Every call is recorded on ``.calls`` so tests can assert
    *how many* LLM calls happened and *which* prompts were sent.
    """

    def __init__(self) -> None:
        self.rules: List[Tuple[str, str]] = []  # (substring, response)
        self.default: str = ""
        self.calls: List[Dict] = []

    def when(self, *, contains: str, returns: str) -> "CannedLLM":
        self.rules.append((contains, returns))
        return self

    def set_default(self, response: str) -> "CannedLLM":
        self.default = response
        return self

    def generate(self, prompt: str, **kwargs) -> str:
        # Mirror the real LLMClient signature; we don't need to do
        # anything with system/temperature/max_tokens here.
        self.calls.append({"prompt": prompt, "kwargs": dict(kwargs)})
        lower = prompt.lower()
        for needle, response in self.rules:
            if needle.lower() in lower:
                return response
        return self.default

    def is_available(self) -> bool:
        return True


@pytest.fixture
def llm_table() -> Callable[[], CannedLLM]:
    """Return a factory that produces a fresh ``CannedLLM`` per test."""
    factory_calls: List[CannedLLM] = []

    def _make() -> CannedLLM:
        c = CannedLLM()
        factory_calls.append(c)
        return c

    return _make


# ---------------------------------------------------------------------------
# Judge-event buffer hygiene
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def clean_judge_events():
    """Drain the judge-event ring buffer and pair cache around each test.

    Autouse is OFF so tests that don't touch the evaluator don't pay the
    import cost.  Tests that do touch the evaluator should declare the
    fixture explicitly.
    """
    from core.evaluator import reset_judge_events

    reset_judge_events()
    yield
    reset_judge_events()
