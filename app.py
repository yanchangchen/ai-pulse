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


# Initialize logger
logger = setup_logger(__name__)

# Page configuration
st.set_page_config(
    page_title="AI Pulse",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for styling
st.markdown("""
<style>
    .theme-card {
        padding: 25px;
        border-radius: 12px;
        margin-bottom: 20px;
        border-left: 8px solid;
        background-color: #ffffff;
        box-shadow: 0 4px 12px rgba(0,0,0,0.08);
        transition: transform 0.2s;
    }
    .theme-card:hover {
        transform: translateY(-2px);
    }
    .stAlert {
        padding: 10px;
        border-radius: 10px;
    }
    .metric-card {
        text-align: center;
        padding: 20px;
        background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
        border-radius: 12px;
        border: 1px solid #dee2e6;
    }
    h1, h2, h3 {
        font-family: 'Outfit', 'Inter', sans-serif;
        font-weight: 700;
        color: #1a1a1a;
    }
    .stMarkdown p {
        line-height: 1.6;
        font-size: 16px;
    }
</style>
""", unsafe_allow_html=True)

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

    # Try to load the data from persistence cache into session state
    if not st.session_state.data_loaded:
        load_data()


def load_data() -> bool:
    """Load data from historical run cache or trigger background refresher."""

    print("\n" + "="*60, flush=True)
    print("⚡ [AI Pulse] Initializing News Intelligence Loader...", flush=True)
    print("="*60, flush=True)

    # Check for historical run in persistence layer
    last_run = get_last_run()
    if last_run and 'full_articles' in last_run['data']:
        last_run_time = get_last_run_time()
        hours_since = (datetime.now() - last_run_time).total_seconds() / 3600 if last_run_time else 999
        
        # Load the cache immediately into session state
        print(f"📦 [CACHE] SUCCESS: Loaded past run from history.json (Created {hours_since:.1f} hours ago at {last_run['timestamp']})", flush=True)
        logger.info("Restoring state from persistence cache (last run was %0.1f hours ago)", hours_since)
        st.session_state.articles = last_run['data']['full_articles']
        st.session_state.themed_articles = last_run['data']['themed_articles']
        st.session_state.summaries = last_run['data']['summaries']
        st.session_state.data_loaded = True
        st.session_state.loaded_timestamp = last_run['timestamp']
        
        # If the cache is expired (>= 6 hours) or we are forced to refresh, trigger the background update
        if hours_since >= 6 or st.session_state.get('force_refresh', False):
            print("⏳ [CACHE] EXPIRED: Persistence data is older than 6 hours. Launching background refresher thread...", flush=True)
            st.session_state.force_refresh = False
            BackgroundRefresher.start()
            st.toast("🔄 Cache is older than 6 hours. Fetching fresh insights in the background...", icon="ℹ️")
        else:
            print("✅ [CACHE] VALID: Loaded current data from persistence cache without background thread trigger.", flush=True)
            st.toast(f"✅ Loaded from cache ({int(hours_since)}h ago)")
        
        print("="*60 + "\n", flush=True)
        return True

    # No historical cache found. Trigger background refresher thread automatically!
    print("⏳ [CACHE] ABSENT: No historical persistence cache found. Launching background refresher thread...", flush=True)
    BackgroundRefresher.start()
    print("="*60 + "\n", flush=True)
    return False


def main() -> None:
    """Main application entry point."""
    init_session_state()
    
    from core.bg_refresher import check_and_show_bg_status, render_sidebar_info
    
    # 1. Top of page alert if background update finished
    check_and_show_bg_status()

    # Title banner
    st.title("⚡ AI Pulse")
    st.markdown("### AI News Intelligence Dashboard")
    st.markdown("---")

    # Sidebar
    with st.sidebar:
        st.header("📊 Dashboard Controls")

        # Refresh button
        if st.button("🔄 Refresh Data"):
            logger.info("Manual background refresh triggered from sidebar.")
            from core.bg_refresher import BackgroundRefresher
            clear_all_caches()
            BackgroundRefresher.start()
            st.toast("🔄 Background refresh started! Keep using the dashboard while we compile new insights.", icon="ℹ️")
            st.rerun()

        # Navigation
        st.subheader("🧭 Navigation")
        st.page_link("app.py", label="Home", icon="🏠")
        st.page_link("pages/1_Overview.py", label="Overview", icon="📋")
        st.page_link("pages/2_Deep_Dive.py", label="Deep Dive", icon="🔍")
        st.page_link("pages/3_Word_Clouds.py", label="Word Clouds", icon="☁️")
        st.page_link("pages/4_Sources.py", label="Sources", icon="📰")
        st.page_link("pages/5_History.py", label="Memory Wiki", icon="🧠")
        st.page_link("pages/6_Trend_Analytics.py", label="Trend Analytics", icon="📈")

        st.divider()

        # Date range display
        st.subheader("📅 Data Period")
        st.write(f"Past **{DAYS_LOOKBACK} days**")

        st.divider()

        # Article counts by theme
        st.subheader("📈 Coverage by Theme")

        if st.session_state.data_loaded and st.session_state.themed_articles:
            theme_counts = {theme: len(st.session_state.themed_articles.get(theme, []))
                          for theme in THEME_ORDER}

            # Display as metric
            total_articles = len(st.session_state.articles)
            st.metric("Total Articles Tracked", total_articles)

            # Legend with counts
            for theme in THEME_ORDER:
                count = theme_counts[theme]
                color = THEME_COLORS.get(theme, '#000')
                # Better label: Remove "AI " prefix if present
                display_label = theme.replace("AI ", "").replace("& ", "").title()
                st.markdown(f"<span style='color:{color}'>●</span> {display_label}: **{count}**", unsafe_allow_html=True)

        st.divider()

        # Ollama info
        st.subheader("🤖 Intelligence Engine")
        if _llm_client.is_available():
            st.success("Connected to Ollama Cloud")
            st.caption(f"Model: {_llm_client.model}")
        else:
            st.error("Engine Disconnected")

        # Background status tracker inside the sidebar
        render_sidebar_info()

    # Main content
    if not st.session_state.data_loaded:
        # Check background refresher status
        from core.bg_refresher import BackgroundRefresher
        import time
        status_info = BackgroundRefresher.get_status()
        
        # Check if the cache was generated while we were waiting or before
        last_run = get_last_run()
        if last_run and 'full_articles' in last_run['data']:
            load_data()
            st.rerun()
            return

        # Render premium glassmorphic/card-based first-load ingestion screen
        st.markdown("""
        <div style="padding: 40px; background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%); border-radius: 16px; color: white; margin-bottom: 30px; box-shadow: 0 10px 25px rgba(0,0,0,0.15);">
            <h2 style="color: white; margin-top: 0; font-family: 'Outfit', sans-serif;">⚡ AI Pulse - First-Time Ingestion</h2>
            <p style="font-size: 16px; opacity: 0.9; line-height: 1.6;">Welcome to your AI News Intelligence Dashboard! Since this is your first time launching the application, we are currently setting up your persistent Memory Wiki and fetching high-signal news from our registered engineering blogs and sources.</p>
            <p style="font-size: 14px; opacity: 0.8; font-style: italic;">This intelligence compilation uses advanced content-based filtering, theme classification, and LLM-synthesized context, which usually takes 15–30 seconds. Thank you for your patience!</p>
        </div>
        """, unsafe_allow_html=True)
        
        # Progress card
        progress_text = status_info.get("progress", "Initializing background news intelligence engine...")
        
        if status_info["status"] == "failed":
            st.error(f"❌ Ingestion Failed: {status_info['error']}")
            if st.button("⚡ Retry Ingestion Pipeline", key="loading_retry_btn", use_container_width=True):
                BackgroundRefresher.start()
                st.rerun()
        else:
            # Display real-time progress in a neat and premium alert/info card
            st.info(f"⏳ **Current Ingestion Step:**\n\n```\n{progress_text}\n```")
            
            # Show a beautiful spinner and sleep/rerun
            with st.spinner("Compiling intelligence briefs and building Memory Wiki..."):
                time.sleep(2)
                st.rerun()
        return

    # Navigation links
    st.markdown("### 🚀 Quick Navigation")
    col1, col2, col3, col4 = st.columns(4)
    with col1: st.page_link("pages/1_Overview.py", label="Overview", icon="📋")
    with col2: st.page_link("pages/2_Deep_Dive.py", label="Deep Dive", icon="🔍")
    with col3: st.page_link("pages/3_Word_Clouds.py", label="Word Clouds", icon="☁️")
    with col4: st.page_link("pages/4_Sources.py", label="Sources", icon="📰")
    st.markdown("<br>", unsafe_allow_html=True)
    st.page_link("pages/5_History.py", label="Memory Wiki (Past Developments)", icon="🧠")

    st.markdown("---")

    # Summary stats
    st.subheader("📊 Thematic Pulse")
    cols = st.columns(len(THEME_ORDER))
    for i, theme in enumerate(THEME_ORDER):
        count = len(st.session_state.themed_articles.get(theme, []))
        color = THEME_COLORS.get(theme, '#000')
        
        # Better label: Remove "AI " prefix if present
        display_label = theme.replace("AI ", "").replace("& ", "").upper()
        
        with cols[i]:
            st.markdown(f"""
            <div style="text-align: center; padding: 10px; border: 2px solid {color}; border-radius: 10px;">
                <div style="font-size: 24px; font-weight: bold; color: {color};">{count}</div>
                <div style="font-size: 10px; font-weight: bold; color: #666;">{display_label}</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("---")

    # Theme summaries preview
    st.subheader("📝 Intelligence Preview")
    for theme in THEME_ORDER:
        summary = st.session_state.summaries.get(theme, {})
        articles = st.session_state.themed_articles.get(theme, [])
        if summary and summary.get('what_is_happening'):
            with st.expander(f"📌 {theme} ({len(articles)} articles)"):
                st.markdown(f"**The Signal:** {summary.get('what_is_happening', '')}")
                st.markdown(f"**Significance:** {summary.get('why_it_matters', '')}")


if __name__ == "__main__":
    main()
