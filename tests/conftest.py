"""
Pytest configuration and global fixtures for AI Pulse tests.
"""

from unittest.mock import MagicMock

import pytest
from core.ai_gateway import tracing
from core.llm_client import LLMClient


@pytest.fixture(autouse=True)
def reset_llm_quota_status():
    """Ensure LLM quota status flag is reset before and after every test."""
    LLMClient.reset_quota_status()
    yield
    LLMClient.reset_quota_status()


@pytest.fixture(autouse=True)
def disable_cloud_supabase(monkeypatch):
    """Guarantee tests never read/write the production Supabase project.

    On machines with a local ``.streamlit/secrets.toml``, importing
    ``config.settings`` touches ``st.secrets``, and Streamlit's
    ``Secrets._parse()`` unconditionally exports every secret — including
    ``SUPABASE_URL`` / ``SUPABASE_KEY`` — into ``os.environ``.
    ``core.supabase_client`` reads those env vars directly, so without this
    guard any test that forgets to mock the manager singleton silently hits
    the real cloud database (this once polluted ``processed_articles`` with
    fixture rows). The stub reports ``is_available() == False``, steering
    Supabase-preferred code paths through their local fallbacks.

    Tests that exercise Supabase-backed behaviour patch
    ``core.supabase_client.get_supabase_manager`` with their own mock, which
    overrides this stub (see ``test_processed_articles.py``).
    """
    offline = MagicMock()
    offline.is_available.return_value = False
    monkeypatch.setattr("core.supabase_client._manager", offline)


@pytest.fixture(autouse=True)
def disable_gateway_tracing(monkeypatch):
    """Guarantee tests never export OTel spans to a real Langfuse project.

    Same rationale as ``disable_cloud_supabase``: once real Langfuse keys
    land in ``.streamlit/secrets.toml``, ``config.settings`` resolves them
    and every gateway call would start exporting spans to cloud.langfuse.com
    from inside pytest.  Resetting the tracing singleton and pinning it off
    keeps tests silent; tests that exercise tracing re-enable it explicitly
    with fake keys and an in-memory span exporter
    (see ``test_gateway_tracing.py``).
    """
    tracing.reset_tracing()
    monkeypatch.setattr(tracing, "is_tracing_enabled", lambda: False)
    yield
    tracing.reset_tracing()
