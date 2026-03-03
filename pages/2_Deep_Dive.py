"""
AI Pulse - Deep Dive Page
Shows detailed view for each theme with full summaries and article tables.
"""

import streamlit as st
from datetime import datetime
import pandas as pd

from config.themes import THEME_ORDER, THEME_COLORS

# Page configuration
st.set_page_config(
    page_title="Deep Dive - AI Pulse",
    page_icon="⚡",
    layout="wide"
)


def get_session_data():
    """Get data from session state."""
    if 'themed_articles' not in st.session_state or 'summaries' not in st.session_state:
        st.error("Please return to the main page to load data first.")
        st.stop()

    return st.session_state.themed_articles, st.session_state.summaries


def main():
    """Main deep dive page."""
    themed_articles, summaries = get_session_data()

    # Header
    st.title("🔍 Deep Dive")
    st.markdown("### Detailed Theme Analysis")
    st.markdown("---")

    # Theme selector
    selected_theme = st.selectbox(
        "Select a theme to explore:",
        THEME_ORDER,
        index=0
    )

    # Get theme data
    theme_articles = themed_articles.get(selected_theme, [])
    theme_summary = summaries.get(selected_theme, {})
    theme_color = THEME_COLORS.get(selected_theme, '#1f77b4')

    # Article count warning
    if len(theme_articles) < 3:
        st.warning(f"⚠️ Limited coverage this week with only {len(theme_articles)} articles for this theme.")

    st.markdown("---")

    # Summary sections
    st.markdown(f"<h2 style='color: {theme_color};'>{selected_theme}</h2>", unsafe_allow_html=True)

    # What is happening
    st.subheader("📰 What is happening")
    if theme_summary.get('what_is_happening'):
        st.markdown(theme_summary['what_is_happening'])
    else:
        st.info("No summary available for this theme.")

    st.markdown("---")

    # Why it matters
    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("🎯 Why it matters")
        if theme_summary.get('why_it_matters'):
            st.markdown(theme_summary['why_it_matters'])
        else:
            st.info("No analysis available.")

    with col2:
        st.markdown(f"""
        <div style="text-align: center; padding: 20px; background-color: #f0f2f6; border-radius: 10px;">
            <div style="font-size: 36px; font-weight: bold; color: {theme_color};">{len(theme_articles)}</div>
            <div style="font-size: 14px; color: #666;">Articles</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")

    # What to watch
    st.subheader("👁️ What to watch")
    if theme_summary.get('what_to_watch'):
        # Parse bullet points
        watch_text = theme_summary['what_to_watch']
        if '\n' in watch_text:
            for line in watch_text.split('\n'):
                if line.strip():
                    st.markdown(f"- {line.strip()}")
        else:
            st.markdown(watch_text)
    else:
        st.info("No items to watch.")

    st.markdown("---")

    # Further reading
    st.subheader("📚 Further Reading")
    if theme_summary.get('further_reading'):
        st.markdown(theme_summary['further_reading'])
    else:
        st.info("No further reading suggestions available.")

    st.markdown("---")

    # Article table
    st.subheader("📋 All Articles in This Theme")

    if theme_articles:
        # Create DataFrame
        df_articles = pd.DataFrame([
            {
                "Title": a.get('title', 'Untitled'),
                "Source": a.get('source_name', 'Unknown'),
                "Date": a.get('published_date', 'Unknown')[:10] if a.get('published_date') else 'Unknown',
                "Summary": (a.get('summary', '')[:150] + '...') if a.get('summary') else '',
                "URL": a.get('link', '')
            }
            for a in theme_articles
        ])

        # Display with column configuration
        st.dataframe(
            df_articles,
            column_config={
                "Title": st.column_config.TextColumn("Title", width="medium"),
                "Source": st.column_config.TextColumn("Source", width="small"),
                "Date": st.column_config.TextColumn("Date", width="small"),
                "Summary": st.column_config.TextColumn("Summary", width="large"),
                "URL": st.column_config.LinkColumn("URL", width="small", display_text="🔗 Link")
            },
            hide_index=True,
            use_container_width=True
        )

        # Export option
        csv = df_articles.to_csv(index=False)
        st.download_button(
            label="📥 Download as CSV",
            data=csv,
            file_name=f"{selected_theme.replace(' ', '_')}_articles.csv",
            mime="text/csv"
        )
    else:
        st.info("No articles found for this theme.")

    st.markdown("---")

    # Navigation
    st.markdown("### 🔗 Quick Navigation")
    col1, col2, col3 = st.columns(3)

    with col1:
        st.page_link("app.py", label="🏠 Back to Dashboard", icon="🏠")

    with col2:
        st.page_link("pages/1_Overview.py", label="📋 Theme Overview", icon="📋")

    with col3:
        st.page_link("pages/3_Word_Clouds.py", label="☁️ Word Clouds", icon="☁️")


if __name__ == "__main__":
    main()
