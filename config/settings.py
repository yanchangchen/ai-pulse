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
CACHE_TTL_SECONDS: int = 12 * 3600  # 12 hours

# Maximum concurrent fetch workers
FETCH_WORKERS: int = 8

# Per-feed RSS fetch timeout (seconds).  Each feed gets up to 2 retries on
# transient errors before being skipped.
RSS_FETCH_TIMEOUT: int = 10
RSS_FETCH_RETRIES: int = 2

# ---------------------------------------------------------------------------
# Weekly quality evaluation
# ---------------------------------------------------------------------------
# Default threshold (0..1).  The Quality Evaluation page exposes a slider that
# can override this per-run; the chosen value is stored on the resulting row.
QUALITY_THRESHOLD: float = 0.80
# How many articles per run to send to the Categoriser judge LLM.
EVALUATION_SAMPLE_SIZE: int = 20
# Cap on the number of historical runs evaluated in a single weekly check.
EVALUATION_MAX_RUNS: int = 7
# How often the WeeklyEvaluator background thread wakes up to check whether
# it should run (seconds).  1 hour is plenty — evaluations are weekly.
EVALUATION_CHECK_INTERVAL_SECONDS: int = 3600

# ---------------------------------------------------------------------------
# LLM context window
# ---------------------------------------------------------------------------
# num_ctx passed to Ollama on every generate() call.  4096 covers ~10
# articles (each ~500 chars ≈ 150 tokens) + system prompt + ~1500 tokens
# of output with headroom.  Bump if you increase MAX_ARTICLES_PER_SUMMARY
# below or if you switch to a model with a larger native context.
OLLAMA_NUM_CTX: int = 4096
# Max articles fed into the theme summariser prompt.  Combined with the
# per-article 300-char summary cap in format_articles_for_prompt(), this
# keeps the input well inside OLLAMA_NUM_CTX.
MAX_ARTICLES_PER_SUMMARY: int = 10
# Rough chars-per-token ratio.  Used to budget the input side of num_ctx.
# 3 chars/token is a conservative estimate that errs on the side of
# truncation rather than overflow.
CHARS_PER_TOKEN: int = 3
# Fraction of num_ctx reserved for input (rest is for system + output).
INPUT_BUDGET_FRACTION: float = 0.6

