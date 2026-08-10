"""
AI Pulse - Main Streamlit Entry Point
AI News Intelligence Dashboard
"""

import streamlit as st
from datetime import datetime, timedelta
import pandas as pd

from config.settings import DAYS_LOOKBACK
from config.themes import THEME_ORDER, THEME_COLORS
from core.cache import (
    cache_fetch_news, 
    cache_classify_articles, 
    cache_generate_summaries, 
    clear_all_caches,
    get_articles_hash
)
from core.history_manager import get_last_run, get_last_run_time
from core.llm_client import LLMClient
from core.logger import setup_logger
from core.bg_refresher import BackgroundRefresher
from core.design_system import apply_design_system, sanitize_summary_html
from core.shared_sidebar import render_sidebar_nav


# Initialize logger
logger = setup_logger(__name__)

# Page configuration
st.set_page_config(
    page_title="AI Pulse",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Apply central design system (adaptive tokens, fonts, cards)
apply_design_system()

# Shared LLM client
_llm_client = LLMClient()


def init_session_state() -> None:
    """Initialize session state variables."""
    if 'data_loaded' not in st.session_state:
        st.session_state.data_loaded = False
    if 'articles' not in st.session_state:
        st.session_state.articles = []
    if 'themed_articles' not in st.session_state:
        st.session_state.themed_articles = {}
    if 'summaries' not in st.session_state:
        st.session_state.summaries = {}
    if 'force_refresh' not in st.session_state:
        st.session_state.force_refresh = False
    if 'backfill_attempted' not in st.session_state:
        st.session_state.backfill_attempted = False

    # Backfill Supabase with historical data on first load
    if not st.session_state.backfill_attempted:
        st.session_state.backfill_attempted = True
        try:
            from core.supabase_client import get_supabase_manager
            from core.history_manager import load_full_history

            supabase = get_supabase_manager()
            if supabase.is_available():
                history_data = load_full_history()
                if history_data:
                    stats = supabase.backfill_from_history(history_data)
                    logger.info(f"Supabase backfill stats: {stats}")
        except Exception as e:
            logger.warning(f"Backfill attempt failed: {e}")


def load_data() -> None:
    """Load data from history or trigger fetch."""
    logger.info("load_data() called.")

    last_run = get_last_run()

    if last_run and 'full_articles' in last_run['data']:
        data = last_run['data']
        st.session_state.articles = data['full_articles']
        st.session_state.themed_articles = data.get('themed_articles', {})
        st.session_state.summaries = data.get('summaries', {})
        st.session_state.loaded_timestamp = last_run['timestamp']
        st.session_state.data_loaded = True
        logger.info(f"Loaded {len(st.session_state.articles)} articles from history cache.")
    else:
        logger.info("No history cache found.")
        st.session_state.data_loaded = False


def main() -> None:
    """Main application layout."""
    init_session_state()

    # Shared sidebar navigation & background refresher status
    render_sidebar_nav()

    # 1. Alert if background update finished while user was interacting
    from core.bg_refresher import check_and_show_bg_status
    check_and_show_bg_status()

    # 2. Check if cache expired (> 12 hours) or no data loaded, and trigger background thread
    from core.bg_refresher import BackgroundRefresher, is_cache_expired
    if is_cache_expired() and not BackgroundRefresher.is_running():
        logger.info("Cache expired or missing. Triggering non-blocking background refresh.")
        BackgroundRefresher.start()

    # Initial data load from disk cache if not yet loaded
    if not st.session_state.data_loaded:
        load_data()

    # Main Dashboard Header
    col_h1, col_h2 = st.columns([3, 1])
    with col_h1:
        st.title("⚡ AI Pulse")
        st.markdown("### High-Signal AI Industry Intelligence Engine")
        last_time = get_last_run_time()
        if last_time:
            st.caption(f"📅 Last Intelligence Run: **{last_time}** (Auto-refreshes every 12 hours)")
    with col_h2:
        st.markdown("<div style='height: 25px;'></div>", unsafe_allow_html=True)
        if st.button("⚡ Fetch & Refresh Now", key="main_header_refresh_btn", type="primary", use_container_width=True):
            from core.llm_client import LLMClient
            LLMClient.reset_quota_status()
            try:
                st.cache_data.clear()
            except Exception:
                pass
            st.session_state.force_refresh = True
            BackgroundRefresher.start()
            st.toast("⚡ Started fresh intelligence pipeline!")
            st.rerun()

    st.divider()

    # Main content
    if not st.session_state.data_loaded:
        status_info = BackgroundRefresher.get_status()

        last_run = get_last_run()
        if last_run and 'full_articles' in last_run['data']:
            load_data()
            st.rerun()
            return

        # Ingestion screen
        st.markdown("""
        <div class="sage-intro">
            <h3>⚡ AI Pulse - First-Time Ingestion</h3>
            <p>Welcome to your AI News Intelligence Dashboard! Setting up your persistent Memory Wiki and fetching high-signal news from registered engineering blogs and sources...</p>
        </div>
        """, unsafe_allow_html=True)

        progress_text = status_info.get("progress", "Initializing background news intelligence engine...")

        if status_info["status"] == "failed":
            st.error(f"❌ Ingestion Failed: {status_info['error']}")
            if st.button("⚡ Retry Ingestion Pipeline", key="loading_retry_btn", use_container_width=True):
                BackgroundRefresher.start()
                st.rerun()
        else:
            st.info(f"⏳ **Current Ingestion Step:**\n\n```\n{progress_text}\n```")
            with st.spinner("Compiling intelligence briefs and building Memory Wiki..."):
                import time
                time.sleep(2)
                st.rerun()
        return

    # Summary stats — Responsive 2-row layout
    st.subheader("📊 Thematic Pulse")

    row1_themes = THEME_ORDER[:4]
    row2_themes = THEME_ORDER[4:]

    cols_r1 = st.columns(len(row1_themes))
    for i, theme in enumerate(row1_themes):
        count = len(st.session_state.themed_articles.get(theme, []))
        color = THEME_COLORS.get(theme, '#666')
        with cols_r1[i]:
            st.markdown(f"""
            <div class="theme-card" style="text-align: center; border-color: {color}40 !important;">
                <div style="font-size: 26px; font-weight: 700; color: {color};">{count}</div>
                <div class="card-meta" style="font-weight: 600;">{theme}</div>
            </div>
            """, unsafe_allow_html=True)

    cols_r2 = st.columns(len(row2_themes))
    for i, theme in enumerate(row2_themes):
        count = len(st.session_state.themed_articles.get(theme, []))
        color = THEME_COLORS.get(theme, '#666')
        with cols_r2[i]:
            st.markdown(f"""
            <div class="theme-card" style="text-align: center; border-color: {color}40 !important;">
                <div style="font-size: 26px; font-weight: 700; color: {color};">{count}</div>
                <div class="card-meta" style="font-weight: 600;">{theme}</div>
            </div>
            """, unsafe_allow_html=True)

    st.divider()

    # Theme summaries preview
    st.subheader("📝 Intelligence Preview")
    for theme in THEME_ORDER:
        summary = st.session_state.summaries.get(theme, {})
        articles = st.session_state.themed_articles.get(theme, [])
        color = THEME_COLORS.get(theme, '#666')
        if summary and summary.get('what_is_happening'):
            with st.expander(f"📌 {theme} ({len(articles)} articles)"):
                st.markdown(f"**The Signal:** {sanitize_summary_html(summary.get('what_is_happening', ''))}")
                st.markdown(f"**Significance:** {summary.get('why_it_matters', '')}")


if __name__ == "__main__":
    main()
