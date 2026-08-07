"""
AI Pulse - Overview Page
Shows theme cards with summaries for each of the thematic areas.
"""

import streamlit as st
from datetime import datetime, timedelta

from config.themes import THEME_ORDER, THEME_COLORS
from core.design_system import apply_design_system, sanitize_summary_html
from core.shared_sidebar import render_sidebar_nav
from core.bg_refresher import check_and_show_bg_status

# Page configuration
st.set_page_config(
    page_title="Overview - AI Pulse",
    page_icon="⚡",
    layout="wide"
)

# Apply central design system
apply_design_system()


def get_session_data():
    """Get data from session state."""
    if 'themed_articles' not in st.session_state or 'summaries' not in st.session_state:
        st.switch_page("app.py")
        st.stop()

    return st.session_state.themed_articles, st.session_state.summaries


def main() -> None:
    """Main overview page."""
    themed_articles, summaries = get_session_data()

    # Top of page alert if background update finished
    check_and_show_bg_status()

    # Sidebar Navigation
    render_sidebar_nav()

    # Header
    st.title("📋 Theme Overview")
    st.markdown("### AI News Intelligence — Past Two Weeks")
    st.divider()

    # Date range
    days_lookback = 14
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days_lookback)
    st.info(f"📅 Coverage Period: **{start_date.strftime('%B %d')}** – **{end_date.strftime('%B %d, %Y')}**")

    st.divider()

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
            <div class="theme-card" style="border-top: 3px solid {color1} !important;">
                <div class="card-title" style="color: {color1};">{theme1}</div>
                <div class="card-meta">📰 <b>{len(articles1)}</b> articles tracked this period</div>
                <div style="margin-top: 14px;">
                    <div class="card-section-label" style="color: {color1};">THE LATEST SIGNAL</div>
                    <div style="font-size: 15px; line-height: 1.6;">{sanitize_summary_html(summary1.get('what_is_happening', 'No signal available.'))}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

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
                <div class="theme-card" style="border-top: 3px solid {color2} !important;">
                    <div class="card-title" style="color: {color2};">{theme2}</div>
                    <div class="card-meta">📰 <b>{len(articles2)}</b> articles tracked this period</div>
                    <div style="margin-top: 14px;">
                        <div class="card-section-label" style="color: {color2};">THE LATEST SIGNAL</div>
                        <div style="font-size: 15px; line-height: 1.6;">{sanitize_summary_html(summary2.get('what_is_happening', 'No signal available.'))}</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                with st.expander("🎯 Strategic Significance"):
                    st.markdown(summary2.get('why_it_matters', 'No analysis available.'))

                with st.expander("👁️ Future Outlook (Watchlist)"):
                    st.markdown(summary2.get('what_to_watch', 'No items to watch.'))

        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
