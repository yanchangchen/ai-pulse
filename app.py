"""
AI Pulse - Main Streamlit Entry Point
AI News Intelligence Dashboard
"""

import streamlit as st
from datetime import datetime, timedelta
import pandas as pd
import requests

from config.themes import THEME_ORDER, THEME_COLORS
from config.sources import SOURCES
from core.cache import cache_fetch_news, cache_classify_articles, cache_generate_summaries, clear_all_caches

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
        padding: 20px;
        border-radius: 10px;
        margin-bottom: 15px;
        border-left: 5px solid;
    }
    .stAlert {
        padding: 10px;
    }
    .metric-card {
        text-align: center;
        padding: 15px;
        background-color: #f0f2f6;
        border-radius: 10px;
    }
    h1, h2, h3 {
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)


# Ollama Cloud configuration
OLLAMA_BASE_URL = "https://api.ollama.com"
DEFAULT_MODEL = "qwen3-coder:30b"
OLLAMA_API_KEY = "45beed49227f4ef5af146efb097df093.UN3XitWdoKXnweyM1t7fp6bP"


def get_ollama_api_key():
    """Get API key from config or secrets."""
    return OLLAMA_API_KEY


def check_ollama_available():
    """Check if Ollama Cloud is accessible."""
    try:
        headers = {}
        api_key = get_ollama_api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", headers=headers, timeout=10)
        return response.status_code == 200
    except Exception:
        return False


def get_ollama_model():
    """Get configured Ollama model from secrets or use default."""
    try:
        return st.secrets.get("OLLAMA_MODEL", DEFAULT_MODEL)
    except Exception:
        return DEFAULT_MODEL


def init_session_state():
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


def load_data():
    """Load and process all data."""
    # Check if Ollama Cloud is available
    if not check_ollama_available():
        st.error("⚠️ Unable to connect to Ollama Cloud. Please check your API key and internet connection.")
        return False

    with st.spinner("📥 Fetching AI news from sources..."):
        # Fetch news
        articles = cache_fetch_news(st.session_state.force_refresh)

    with st.spinner("🏷️ Classifying articles into themes..."):
        # Classify articles using Ollama Cloud
        themed_articles = cache_classify_articles(articles, "")

    with st.spinner("📝 Generating theme summaries (this may take a while)..."):
        # Generate summaries
        summaries = cache_generate_summaries(themed_articles, "")

    st.session_state.articles = articles
    st.session_state.themed_articles = themed_articles
    st.session_state.summaries = summaries
    st.session_state.data_loaded = True

    return True


def main():
    """Main application entry point."""
    init_session_state()

    # Title banner
    st.title("⚡ AI Pulse")
    st.markdown("### AI News Intelligence Dashboard")
    st.markdown("---")

    # Calculate date range
    days_lookback = 14
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days_lookback)

    # Sidebar
    with st.sidebar:
        st.header("📊 Dashboard Controls")

        # Refresh button
        if st.button("🔄 Refresh Data"):
            st.session_state.force_refresh = True
            clear_all_caches()
            st.rerun()

        st.divider()

        # Date range display
        st.subheader("📅 Date Range")
        st.write(f"**From:** {start_date.strftime('%B %d, %Y')}")
        st.write(f"**To:** {end_date.strftime('%B %d, %Y')}")

        st.divider()

        # Article counts by theme
        st.subheader("📈 Articles by Theme")

        if st.session_state.data_loaded and st.session_state.themed_articles:
            theme_counts = {theme: len(st.session_state.themed_articles.get(theme, []))
                          for theme in THEME_ORDER}

            # Create DataFrame for bar chart
            df_counts = pd.DataFrame({
                'Theme': [t.split()[0] for t in THEME_ORDER],  # Short names
                'Count': [theme_counts[t] for t in THEME_ORDER]
            })

            # Display as metric
            total_articles = len(st.session_state.articles)
            st.metric("Total Articles", total_articles)

            # Mini bar chart
            st.bar_chart(df_counts.set_index('Theme')['Count'], horizontal=True)

            # Legend with counts
            for theme in THEME_ORDER:
                count = theme_counts[theme]
                color = THEME_COLORS.get(theme, '#000')
                short_name = theme.split()[0]  # First word
                st.markdown(f"<span style='color:{color}'>●</span> {short_name}: **{count}**", unsafe_allow_html=True)

        st.divider()

        # Source filter
        st.subheader("🔍 Filter by Source")
        if st.session_state.data_loaded and st.session_state.articles:
            sources = sorted(list(set(a['source_name'] for a in st.session_state.articles)))
            selected_sources = st.multiselect("Select sources", sources, default=sources)

            if selected_sources:
                filtered_count = len([a for a in st.session_state.articles if a['source_name'] in selected_sources])
                st.write(f"Showing {filtered_count} articles from {len(selected_sources)} sources")
        else:
            st.write("No sources available")

        st.divider()

        # Ollama info
        st.subheader("🤖 Ollama Status")
        if check_ollama_available():
            st.success("✅ Ollama Cloud is connected")
            model = get_ollama_model()
            st.caption(f"Using model: {model}")
        else:
            st.error("❌ Ollama Cloud not connected")
            st.caption("Check your API key in the configuration")

        st.divider()

        # Cache info
        st.caption("📦 Data is cached for 6 hours")

    # Main content
    if not st.session_state.data_loaded:
        # First load
        success = load_data()
        if not success:
            return

    # Show info banner if data loaded
    if 'data_loaded' in st.session_state and st.session_state.data_loaded:
        st.success(f"📊 Showing {len(st.session_state.articles)} articles from the past {days_lookback} days")

    # Show overview link
    st.markdown("### 🚀 Quick Navigation")
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.page_link("pages/1_Overview.py", label="📋 Theme Overview", icon="📋")

    with col2:
        st.page_link("pages/2_Deep_Dive.py", label="🔍 Deep Dive", icon="🔍")

    with col3:
        st.page_link("pages/3_Word_Clouds.py", label="☁️ Word Clouds", icon="☁️")

    with col4:
        st.page_link("pages/4_Sources.py", label="📰 Sources", icon="📰")

    st.markdown("---")

    # Summary stats
    st.subheader("📊 This Week at a Glance")

    col1, col2, col3, col4, col5 = st.columns(5)

    columns = [col1, col2, col3, col4, col5]

    for i, theme in enumerate(THEME_ORDER):
        count = len(st.session_state.themed_articles.get(theme, []))
        color = THEME_COLORS.get(theme, '#000')

        with columns[i]:
            st.markdown(f"""
            <div style="text-align: center; padding: 10px; border: 2px solid {color}; border-radius: 10px;">
                <div style="font-size: 24px; font-weight: bold; color: {color};">{count}</div>
                <div style="font-size: 12px;">{theme.split()[0]} {theme.split()[1] if len(theme.split()) > 1 else ''}</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("---")

    # Theme summaries preview
    st.subheader("📝 Theme Summaries Preview")

    for theme in THEME_ORDER:
        summary = st.session_state.summaries.get(theme, {})
        articles = st.session_state.themed_articles.get(theme, [])

        if summary and summary.get('what_is_happening'):
            with st.expander(f"📌 {theme} ({len(articles)} articles)"):
                st.markdown(f"**What is happening:** {summary.get('what_is_happening', '')}")
                st.markdown(f"**Why it matters:** {summary.get('why_it_matters', '')}")
                st.markdown(f"**What to watch:** {summary.get('what_to_watch', '')}")


if __name__ == "__main__":
    main()
