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
    """Read a config value from Streamlit secrets, then .toml file, then env vars, then default."""
    # 1. Streamlit secrets (only available at runtime inside a Streamlit app)
    try:
        import streamlit as st
        # This will fail or return empty if not running via 'streamlit run'
        value = st.secrets.get(key)
        if value:
            return str(value)
    except Exception:
        pass

    # 2. Direct TOML parse fallback (for CLI/Tests/Scripts)
    try:
        from pathlib import Path
        # Look for .streamlit/secrets.toml relative to project root
        secrets_path = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"
        if secrets_path.exists():
            import toml
            secrets = toml.load(secrets_path)
            value = secrets.get(key)
            if value:
                return str(value)
    except Exception:
        # Fallback to manual line parsing if toml package is missing
        try:
            with open(secrets_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith(key):
                        # Simple "KEY = VALUE" parser
                        parts = line.split("=", 1)
                        if len(parts) == 2:
                            val = parts[1].strip().strip('"').strip("'")
                            if val:
                                return val
        except Exception:
            pass

    # 3. Environment variable
    value = os.environ.get(key)
    if value:
        return value

    # 4. Default
    return default


# ---------------------------------------------------------------------------
# Ollama Cloud configuration
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL: str = _get_secret("OLLAMA_BASE_URL", "https://api.ollama.com")
OLLAMA_MODEL: str = _get_secret("OLLAMA_MODEL", "minimax-m3:cloud")
OLLAMA_API_KEY: str = _get_secret("OLLAMA_API_KEY", "")

# ---------------------------------------------------------------------------
# App-level constants
# ---------------------------------------------------------------------------
DAYS_LOOKBACK: int = 14
CACHE_TTL_SECONDS: int = 6 * 3600  # 6 hours

# Maximum concurrent fetch workers
FETCH_WORKERS: int = 8

