"""
AI Pulse - Sources Page
Shows all sources and articles grouped by source.
"""

import streamlit as st
import pandas as pd

from config.sources import SOURCES
from config.themes import THEME_ORDER, THEME_COLORS

# Page configuration
st.set_page_config(
    page_title="Sources - AI Pulse",
    page_icon="⚡",
    layout="wide"
)


def get_session_data():
    """Get data from session state."""
    if 'articles' not in st.session_state:
        st.error("Please return to the main page to load data first.")
        st.stop()

    return st.session_state.articles


def main():
    """Main sources page."""
    articles = get_session_data()

    # Header
    st.title("📰 Sources")
    st.markdown("### All News Sources and Their Articles")
    st.markdown("---")

    # Source statistics
    source_stats = {}
    for article in articles:
        source = article.get('source_name', 'Unknown')
        if source not in source_stats:
            source_stats[source] = {'count': 0, 'url': ''}

        # Try to find URL from config
        for s in SOURCES:
            if s['name'] == source:
                source_stats[source]['url'] = s.get('url', '')
                source_stats[source]['type'] = s.get('type', 'unknown')
                source_stats[source]['category'] = s.get('category', 'unknown')
                break

        source_stats[source]['count'] += 1

    # Sources table
    st.subheader("📊 Source Overview")

    # Create DataFrame for sources
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

    st.markdown("---")

    # Articles by source
    st.subheader("📋 Articles by Source")

    # Group articles by source
    sources_sorted = sorted(source_stats.items(), key=lambda x: x[1]['count'], reverse=True)

    # Source selector
    source_names = [s[0] for s in sources_sorted]
    selected_source = st.selectbox(
        "Select a source:",
        ["All Sources"] + source_names
    )

    if selected_source == "All Sources":
        # Show all articles grouped by source
        for source_name, stats in sources_sorted:
            source_articles = [a for a in articles if a.get('source_name') == source_name]

            if not source_articles:
                continue

            st.markdown(f"### 📌 {source_name} ({len(source_articles)} articles)")

            # Show theme badges for this source
            theme_counts = {}
            for a in source_articles:
                theme = a.get('theme', 'Unknown')
                theme_counts[theme] = theme_counts.get(theme, 0) + 1

            # Display theme badges
            theme_badge_cols = st.columns(len(theme_counts))
            for j, (theme, count) in enumerate(theme_counts.items()):
                color = THEME_COLORS.get(theme, '#666')
                with theme_badge_cols[j]:
                    st.markdown(f"<span style='background-color: {color}20; color: {color}; padding: 5px 10px; border-radius: 5px; font-size: 12px;'>{theme.split()[0]}: {count}</span>", unsafe_allow_html=True)

            # Show articles
            for article in source_articles[:10]:  # Limit to 10 per source
                theme = article.get('theme', 'Unknown')
                theme_color = THEME_COLORS.get(theme, '#666')

                with st.expander(f"📰 {article.get('title', 'Untitled')[:80]}..."):
                    st.markdown(f"**Theme:** <span style='color: {theme_color};'>{theme}</span>", unsafe_allow_html=True)
                    st.markdown(f"**Date:** {article.get('published_date', 'Unknown')[:10]}")
                    if article.get('summary'):
                        st.markdown(f"**Summary:** {article['summary']}")
                    if article.get('link'):
                        st.markdown(f"[Read Full Article]({article['link']})")

            st.markdown("<br>", unsafe_allow_html=True)
    else:
        # Show articles for selected source
        source_articles = [a for a in articles if a.get('source_name') == selected_source]

        # Theme breakdown
        theme_counts = {}
        for a in source_articles:
            theme = a.get('theme', 'Unknown')
            theme_counts[theme] = theme_counts.get(theme, 0) + 1

        st.markdown(f"### 📌 {selected_source} ({len(source_articles)} articles)")

        # Theme badges
        theme_cols = st.columns(len(theme_counts))
        for j, (theme, count) in enumerate(theme_counts.items()):
            color = THEME_COLORS.get(theme, '#666')
            with theme_cols[j]:
                st.markdown(f"<span style='background-color: {color}20; color: {color}; padding: 5px 10px; border-radius: 5px;'>{theme.split()[0]}: {count}</span>", unsafe_allow_html=True)

        st.markdown("---")

        # Article table
        df_source_articles = pd.DataFrame([
            {
                "Title": a.get('title', 'Untitled'),
                "Theme": a.get('theme', 'Unknown'),
                "Date": a.get('published_date', 'Unknown')[:10] if a.get('published_date') else 'Unknown',
                "Summary": (a.get('summary', '')[:100] + '...') if a.get('summary') else '',
                "URL": a.get('link', '')
            }
            for a in source_articles
        ])

        st.dataframe(
            df_source_articles,
            column_config={
                "Title": "Article Title",
                "Theme": "Theme",
                "Date": "Date",
                "Summary": "Summary",
                "URL": st.column_config.LinkColumn("Link", display_text="🔗")
            },
            hide_index=True,
            use_container_width=True
        )

    st.markdown("---")

    # Navigation
    st.markdown("### 🔗 Quick Navigation")
    col1, col2, col3 = st.columns(3)

    with col1:
        st.page_link("app.py", label="🏠 Back to Dashboard", icon="🏠")

    with col2:
        st.page_link("pages/1_Overview.py", label="📋 Theme Overview", icon="📋")

    with col3:
        st.page_link("pages/2_Deep_Dive.py", label="🔍 Deep Dive", icon="🔍")


if __name__ == "__main__":
    main()
