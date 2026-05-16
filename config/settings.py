"""
Centralized settings for AI Pulse.
Single source of truth for Ollama configuration, API keys, and app constants.

Resolution order: st.secrets → os.environ → defaults (fail gracefully).
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _get_secret(key: str, default: str = "") -> str:
    """Read a config value from Streamlit secrets, then env vars, then default."""
    # 1. Streamlit secrets (only available at runtime inside a Streamlit app)
    try:
        import streamlit as st
        value = st.secrets.get(key)
        if value:
            return str(value)
    except Exception:
        pass

    # 2. Environment variable
    value = os.environ.get(key)
    if value:
        return value

    # 3. Default
    return default


# ---------------------------------------------------------------------------
# Ollama Cloud configuration
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL: str = _get_secret("OLLAMA_BASE_URL", "https://api.ollama.com")
OLLAMA_MODEL: str = _get_secret("OLLAMA_MODEL", "qwen3.5:cloud")
OLLAMA_API_KEY: str = _get_secret("OLLAMA_API_KEY", "")

# ---------------------------------------------------------------------------
# App-level constants
# ---------------------------------------------------------------------------
DAYS_LOOKBACK: int = 14
CACHE_TTL_SECONDS: int = 6 * 3600  # 6 hours

# Maximum concurrent fetch workers
FETCH_WORKERS: int = 8
