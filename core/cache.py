"""
Caching module for AI Pulse.
Provides caching utilities using st.cache_data with disk-based JSON persistence
for surviving restarts and content-based hashing to optimize LLM calls.
"""

import json
import hashlib
import os
from pathlib import Path
from typing import Any, List, Dict

import streamlit as st

from config.settings import CACHE_TTL_SECONDS
from core.logger import setup_logger

logger = setup_logger(__name__)

# Cache TTL in seconds (6 hours)
CACHE_TTL = CACHE_TTL_SECONDS

# Disk cache directory (next to the project root)
_DISK_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"


def _ensure_cache_dir() -> Path:
    """Create disk cache directory if it doesn't exist."""
    _DISK_CACHE_DIR.mkdir(exist_ok=True)
    return _DISK_CACHE_DIR


def _disk_cache_path(key: str) -> Path:
    """Return the path for a given cache key."""
    safe_name = hashlib.md5(key.encode()).hexdigest()
    return _ensure_cache_dir() / f"{safe_name}.json"


def save_to_disk(key: str, data: Any) -> None:
    """Persist data to a JSON file keyed by *key*."""
    try:
        path = _disk_cache_path(key)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
        logger.debug("Disk cache saved: %s", key)
    except Exception as exc:
        logger.warning("Failed to write disk cache for %s: %s", key, exc)


def load_from_disk(key: str) -> Any:
    """Load data from the disk cache, or return None if missing/corrupt."""
    try:
        path = _disk_cache_path(key)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        logger.warning("Failed to read disk cache for %s: %s", key, exc)
    return None

def get_articles_hash(articles: List[Dict]) -> str:
    """Generate a stable hash for a list of articles based on titles and links."""
    if not articles:
        return "empty"
    # Sort and join identifying info
    ids = sorted([f"{a.get('title', '')}{a.get('link', '')}" for a in articles])
    return hashlib.sha256("".join(ids).encode()).hexdigest()

# ---------------------------------------------------------------------------
# Streamlit-cached wrappers
# ---------------------------------------------------------------------------

@st.cache_data(ttl=CACHE_TTL)
def cache_fetch_news() -> list:
    """Cache news fetching with 6-hour TTL."""
    from core.fetcher import fetch_all_news
    logger.info("Executing fresh news fetch from sources...")
    articles = fetch_all_news()
    save_to_disk("fetch_news", articles)
    return articles


@st.cache_data(ttl=CACHE_TTL)
def cache_classify_articles(articles: list, articles_hash: str) -> dict:
    """
    Cache article classification with 6-hour TTL.
    Uses articles_hash as a key to prevent re-classification if content hasn't changed.
    """
    from core.classifier import classify_articles
    logger.info("Classifying articles with content hash: %s...", articles_hash[:8])
    result = classify_articles(articles)
    save_to_disk(f"classify_{articles_hash}", result)
    return result


@st.cache_data(ttl=CACHE_TTL)
def cache_generate_summaries(themed_articles: dict, articles: list, articles_hash: str) -> dict:
    """
    Cache summary generation with 6-hour TTL.
    Uses articles_hash as a key to skip LLM calls if content is unchanged.
    """
    from core.summariser import generate_all_summaries
    logger.info("Generating LLM summaries with content hash: %s...", articles_hash[:8])
    result = generate_all_summaries(themed_articles, articles)
    save_to_disk(f"summaries_{articles_hash}", result)
    return result


@st.cache_data(ttl=CACHE_TTL)
def cache_wordclouds(themed_articles: dict, articles_hash: str) -> dict:
    """Cache word cloud generation with 6-hour TTL."""
    from core.visualiser import generate_all_wordclouds
    logger.info("Generating wordclouds with content hash: %s...", articles_hash[:8])
    result = generate_all_wordclouds(themed_articles)
    save_to_disk(f"wordclouds_{articles_hash}", result)
    return result


def clear_all_caches() -> None:
    """Clear all Streamlit caches."""
    logger.warning("Application-wide cache clear triggered.")
    st.cache_data.clear()


def get_cache_info() -> dict:
    """Get information about cache status."""
    return {
        "ttl_seconds": CACHE_TTL,
        "ttl_hours": CACHE_TTL / 3600,
        "disk_cache_dir": str(_DISK_CACHE_DIR),
        "note": "Data is cached for 6 hours",
    }
