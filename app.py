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
from core.llm_client import LLMClient
from core.logger import setup_logger

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


def load_data() -> bool:
    """Load and process all data with content-based caching optimization."""
    # Check if Ollama Cloud is available
    if not _llm_client.is_available():
        st.error("⚠️ Unable to connect to Ollama Cloud. Please check your API key and internet connection.")
        return False

    with st.spinner("📥 Fetching AI news from sources..."):
        articles = cache_fetch_news()
        
    if not articles:
        st.warning("No articles found in the last 14 days.")
        return False

    # Generate hash for token optimization
    articles_hash = get_articles_hash(articles)
    logger.info("Signal processed with hash: %s", articles_hash[:8])

    with st.spinner("🏷️ Classifying articles into themes..."):
        themed_articles = cache_classify_articles(articles, articles_hash)

    with st.spinner("📝 Generating theme summaries (token optimized)..."):
        summaries = cache_generate_summaries(themed_articles, articles_hash)

    st.session_state.articles = articles
    st.session_state.themed_articles = themed_articles
    st.session_state.summaries = summaries
    st.session_state.data_loaded = True

    return True


def main() -> None:
    """Main application entry point."""
    init_session_state()

    # Title banner
    st.title("⚡ AI Pulse")
    st.markdown("### AI News Intelligence Dashboard")
    st.markdown("---")

    # Sidebar
    with st.sidebar:
        st.header("📊 Dashboard Controls")

        # Refresh button
        if st.button("🔄 Refresh Data"):
            logger.info("Manual cache clear triggered from sidebar.")
            st.session_state.force_refresh = True
            clear_all_caches()
            st.rerun()

        # Navigation
        st.subheader("🧭 Navigation")
        st.page_link("app.py", label="🏠 Home", icon="🏠")
        st.page_link("pages/1_Overview.py", label="📋 Overview", icon="📋")
        st.page_link("pages/2_Deep_Dive.py", label="🔍 Deep Dive", icon="🔍")
        st.page_link("pages/3_Word_Clouds.py", label="☁️ Word Clouds", icon="☁️")
        st.page_link("pages/4_Sources.py", label="📰 Sources", icon="📰")
        st.page_link("pages/5_History.py", label="🧠 Memory Wiki", icon="🧠")

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
                short_name = theme.split()[0]  # First word
                st.markdown(f"<span style='color:{color}'>●</span> {short_name}: **{count}**", unsafe_allow_html=True)

        st.divider()

        # Ollama info
        st.subheader("🤖 Intelligence Engine")
        if _llm_client.is_available():
            st.success("Connected to Ollama Cloud")
            st.caption(f"Model: {_llm_client.model}")
        else:
            st.error("Engine Disconnected")

        st.divider()
        st.caption("📦 Signal cached for 6 hours")

    # Main content
    if not st.session_state.data_loaded:
        success = load_data()
        if not success:
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
