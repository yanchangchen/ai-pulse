"""
Caching module for AI Pulse.
Provides caching utilities using st.cache_data.
"""

import streamlit as st
from typing import Any, Callable
from datetime import datetime, timedelta

# Cache TTL in seconds (6 hours)
CACHE_TTL = 6 * 3600


@st.cache_data(ttl=CACHE_TTL)
def cache_fetch_news(_force_refresh: bool = False) -> list:
    """Cache news fetching with 6-hour TTL."""
    from core.fetcher import fetch_all_news
    return fetch_all_news()


@st.cache_data(ttl=CACHE_TTL)
def cache_classify_articles(articles: list, api_key: str) -> dict:
    """Cache article classification with 6-hour TTL."""
    from core.classifier import classify_articles
    return classify_articles(articles, api_key)


@st.cache_data(ttl=CACHE_TTL)
def cache_generate_summaries(themed_articles: dict, api_key: str) -> dict:
    """Cache summary generation with 6-hour TTL."""
    from core.summariser import generate_all_summaries
    return generate_all_summaries(themed_articles, api_key)


@st.cache_data(ttl=CACHE_TTL)
def cache_wordclouds(themed_articles: dict) -> dict:
    """Cache word cloud generation with 6-hour TTL."""
    from core.visualiser import generate_all_wordclouds
    return generate_all_wordclouds(themed_articles)


def clear_all_caches():
    """Clear all Streamlit caches."""
    cache_fetch_news.clear()
    cache_classify_articles.clear()
    cache_generate_summaries.clear()
    cache_wordclouds.clear()


def get_cache_info():
    """Get information about cache status."""
    # Check if data is from cache
    try:
        # This is a workaround since st.cache_data doesn't expose cache status directly
        return {
            "ttl_seconds": CACHE_TTL,
            "ttl_hours": CACHE_TTL / 3600,
            "note": "Data is cached for 6 hours"
        }
    except Exception:
        return {"error": "Unable to get cache info"}
