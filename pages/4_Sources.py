"""
AI Pulse - Sources Page
Shows all sources and articles grouped by source.
"""

import streamlit as st
import pandas as pd

from config.sources import SOURCES
from config.themes import THEME_ORDER, THEME_COLORS
from core.design_system import apply_design_system
from core.shared_sidebar import render_sidebar_nav
from core.bg_refresher import check_and_show_bg_status

# Page configuration
st.set_page_config(
    page_title="Sources - AI Pulse",
    page_icon="⚡",
    layout="wide"
)

# Apply central design system
apply_design_system()


def get_session_data():
    """Get data from session state."""
    if 'articles' not in st.session_state:
        st.switch_page("app.py")
        st.stop()

    return st.session_state.articles


def main() -> None:
    """Main sources page."""
    articles = get_session_data()

    # Top of page alert if background update finished
    check_and_show_bg_status()

    # Sidebar Navigation
    render_sidebar_nav()

    # Header
    st.title("📰 Sources Explorer")
    st.markdown("### Registered Engineering Blogs & RSS Feeds")
    st.divider()

    # Source statistics
    source_stats = {}
    for article in articles:
        source = article.get('source_name', 'Unknown')
        if source not in source_stats:
            source_stats[source] = {'count': 0, 'url': ''}

        for s in SOURCES:
            if s['name'] == source:
                source_stats[source]['url'] = s.get('url', '')
                source_stats[source]['type'] = s.get('type', 'unknown')
                source_stats[source]['category'] = s.get('category', 'unknown')
                break

        source_stats[source]['count'] += 1

    # Sources table
    st.subheader("📊 Source Coverage Overview")

    source_data = []
    for source_name, stats in sorted(source_stats.items(), key=lambda x: x[1]['count'], reverse=True):
        source_data.append({
            "Name": source_name,
            "Type": stats.get('type', 'unknown').upper(),
            "Category": stats.get('category', 'unknown').title(),
            "Articles": stats['count'],
            "Link": stats.get('url', '')
        })

    if source_data:
        df_sources = pd.DataFrame(source_data)

        st.dataframe(
            df_sources,
            column_config={
                "Name": "Source Name",
                "Type": st.column_config.TextColumn("Type", width="small"),
                "Category": st.column_config.TextColumn("Category", width="small"),
                "Articles": st.column_config.ProgressColumn("Articles", format="%d", min_value=0, max_value=max(s['count'] for s in source_stats.values())),
                "Link": st.column_config.LinkColumn("Link to Feed", width="small", display_text="🔗")
            },
            hide_index=True,
            use_container_width=True
        )

    st.divider()

    # Articles by source
    col_s_head, col_s_reset = st.columns([4, 1])
    with col_s_head:
        st.subheader("📋 Articles by Source")
    with col_s_reset:
        if st.button("🔄 Reset Source Filter", key="btn_reset_source_filter"):
            if "sel_source_filter" in st.session_state:
                del st.session_state["sel_source_filter"]
            st.rerun()

    sources_sorted = sorted(source_stats.items(), key=lambda x: x[1]['count'], reverse=True)
    source_names = [s[0] for s in sources_sorted]
    selected_source = st.selectbox(
        "Select a source to filter:",
        ["All Sources"] + source_names,
        key="sel_source_filter"
    )

    if selected_source == "All Sources":
        for source_name, stats in sources_sorted:
            source_articles = [a for a in articles if a.get('source_name') == source_name]

            if not source_articles:
                continue

            st.markdown(f"### 📌 {source_name} ({len(source_articles)} articles)")

            theme_counts = {}
            for a in source_articles:
                theme = a.get('theme', 'Unknown')
                theme_counts[theme] = theme_counts.get(theme, 0) + 1

            for article in source_articles[:10]:
                theme = article.get('theme', 'Unknown')
                theme_color = THEME_COLORS.get(theme, '#666')

                with st.expander(f"📰 {article.get('title', 'Untitled')[:80]}..."):
                    st.markdown(f"**Theme:** <span class='theme-pill' style='background-color: {theme_color};'>{theme}</span>", unsafe_allow_html=True)
                    st.markdown(f"**Date:** {article.get('published_date', 'Unknown')[:10]}")
                    if article.get('summary'):
                        st.markdown(f"**Summary:** {article['summary']}")
                    if article.get('link'):
                        st.markdown(f"[Read Full Article]({article['link']})")

            st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    else:
        source_articles = [a for a in articles if a.get('source_name') == selected_source]

        theme_counts = {}
        for a in source_articles:
            theme = a.get('theme', 'Unknown')
            theme_counts[theme] = theme_counts.get(theme, 0) + 1

        st.markdown(f"### 📌 {selected_source} ({len(source_articles)} articles)")

        for article in source_articles:
            theme = article.get('theme', 'Unknown')
            theme_color = THEME_COLORS.get(theme, '#666')

            with st.expander(f"📰 {article.get('title', 'Untitled')}"):
                st.markdown(f"**Theme:** <span class='theme-pill' style='background-color: {theme_color};'>{theme}</span>", unsafe_allow_html=True)
                st.markdown(f"**Date:** {article.get('published_date', 'Unknown')[:10]}")
                if article.get('summary'):
                    st.markdown(f"**Summary:** {article['summary']}")
                if article.get('link'):
                    st.markdown(f"[Read Full Article]({article['link']})")


if __name__ == "__main__":
    main()
