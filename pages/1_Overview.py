"""
AI Pulse - Overview Page
Shows theme cards with summaries for each of the 5 thematic areas.
"""

import streamlit as st
from datetime import datetime, timedelta

from config.themes import THEME_ORDER, THEME_COLORS

# Page configuration
st.set_page_config(
    page_title="Overview - AI Pulse",
    page_icon="⚡",
    layout="wide"
)

# Custom CSS for theme cards
st.markdown("""
<style>
    .theme-card {
        padding: 20px;
        border-radius: 10px;
        margin-bottom: 20px;
        background-color: #f8f9fa;
        border-left: 5px solid;
    }
    .theme-header {
        font-size: 18px;
        font-weight: bold;
        margin-bottom: 10px;
    }
    .article-count {
        font-size: 14px;
        color: #666;
        margin-bottom: 15px;
    }
    .summary-section {
        margin-top: 15px;
    }
    .summary-label {
        font-weight: bold;
        font-size: 13px;
        color: #444;
        margin-bottom: 5px;
    }
</style>
""", unsafe_allow_html=True)


def get_session_data():
    """Get data from session state."""
    if 'themed_articles' not in st.session_state or 'summaries' not in st.session_state:
        st.switch_page("app.py")
        st.stop()

    return st.session_state.themed_articles, st.session_state.summaries


def main() -> None:
    """Main overview page."""
    themed_articles, summaries = get_session_data()

    from core.bg_refresher import check_and_show_bg_status
    from core.shared_sidebar import render_sidebar_nav

    # 1. Top of page alert if background update finished
    check_and_show_bg_status()

    # Sidebar Navigation
    render_sidebar_nav()

    # Header
    st.title("📋 Theme Overview")
    st.markdown("### AI News Intelligence - Past Two Weeks")
    st.markdown("---")

    # Date range
    days_lookback = 14
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days_lookback)
    st.info(f"📅 Coverage: {start_date.strftime('%B %d')} - {end_date.strftime('%B %d, %Y')}")

    st.markdown("---")

    # Theme cards in 2-column grid
    for i in range(0, len(THEME_ORDER), 2):
        col1, col2 = st.columns(2)

        # First theme in row
        theme1 = THEME_ORDER[i]
        color1 = THEME_COLORS.get(theme1, '#1f77b4')
        summary1 = summaries.get(theme1, {})
        articles1 = themed_articles.get(theme1, [])

        with col1:
            st.markdown(f"""
            <div class="theme-card" style="border-left-color: {color1};">
                <div class="theme-header" style="color: {color1};">{theme1}</div>
                <div class="article-count">📰 {len(articles1)} articles tracked this period</div>
                <div style="margin-top: 15px;">
                    <div style="font-weight: bold; color: {color1}; font-size: 14px; margin-bottom: 5px;">THE LATEST SIGNAL</div>
                    <div style="font-size: 16px; line-height: 1.6;">{summary1.get('what_is_happening')}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # Expandable sections for deeper insights
            with st.expander("🎯 Strategic Significance"):
                st.markdown(summary1.get('why_it_matters', 'No analysis available.'))

            with st.expander("👁️ Future Outlook (Watchlist)"):
                st.markdown(summary1.get('what_to_watch', 'No items to watch.'))

        # Second theme in row (if exists)
        if i + 1 < len(THEME_ORDER):
            theme2 = THEME_ORDER[i + 1]
            color2 = THEME_COLORS.get(theme2, '#9467bd')
            summary2 = summaries.get(theme2, {})
            articles2 = themed_articles.get(theme2, [])

            with col2:
                st.markdown(f"""
                <div class="theme-card" style="border-left-color: {color2};">
                    <div class="theme-header" style="color: {color2};">{theme2}</div>
                    <div class="article-count">📰 {len(articles2)} articles tracked this period</div>
                    <div style="margin-top: 15px;">
                        <div style="font-weight: bold; color: {color2}; font-size: 14px; margin-bottom: 5px;">THE LATEST SIGNAL</div>
                        <div style="font-size: 16px; line-height: 1.6;">{summary2.get('what_is_happening')}</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                # Expandable sections
                with st.expander("🎯 Strategic Significance"):
                    st.markdown(summary2.get('why_it_matters', 'No analysis available.'))

                with st.expander("👁️ Future Outlook (Watchlist)"):
                    st.markdown(summary2.get('what_to_watch', 'No items to watch.'))

        st.markdown("<br>", unsafe_allow_html=True)

    st.markdown("---")

    # Navigation
    st.markdown("### 🔗 Quick Navigation")
    col1, col2, col3 = st.columns(3)

    with col1:
        st.page_link("app.py", label="Back to Dashboard", icon="🏠")

    with col2:
        st.page_link("pages/2_Deep_Dive.py", label="Deep Dive", icon="🔍")

    with col3:
        st.page_link("pages/3_Word_Clouds.py", label="Word Clouds", icon="☁️")


if __name__ == "__main__":
    main()
