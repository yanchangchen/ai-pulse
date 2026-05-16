"""
AI Pulse - Word Clouds Page
Shows trending topic word clouds for each theme.
"""

import streamlit as st
import pandas as pd

from config.themes import THEME_ORDER, THEME_COLORS
from core.visualiser import (
    generate_wordcloud,
    get_top_words_for_theme,
    create_word_frequency_chart
)

# Page configuration
st.set_page_config(
    page_title="Word Clouds - AI Pulse",
    page_icon="⚡",
    layout="wide"
)


def get_session_data():
    """Get data from session state."""
    if 'themed_articles' not in st.session_state:
        st.error("Please return to the main page to load data first.")
        st.stop()

    return st.session_state.themed_articles


def main() -> None:
    """Main word clouds page."""
    themed_articles = get_session_data()

    # Sidebar Navigation
    with st.sidebar:
        st.header("🧭 Navigation")
        st.page_link("app.py", label="Home", icon="🏠")
        st.page_link("pages/1_Overview.py", label="Overview", icon="📋")
        st.page_link("pages/2_Deep_Dive.py", label="Deep Dive", icon="🔍")
        st.page_link("pages/3_Word_Clouds.py", label="Word Clouds", icon="☁️")
        st.page_link("pages/4_Sources.py", label="Sources", icon="📰")
        st.page_link("pages/5_History.py", label="Memory Wiki", icon="🧠")

    # Header
    st.title("☁️ Trending Topics")
    st.markdown("### Word Clouds and Top Keywords by Theme")
    st.markdown("---")

    # View mode selector
    view_mode = st.radio(
        "View Mode:",
        ["All Word Clouds", "Single Theme Enlarged"],
        horizontal=True
    )

    if view_mode == "Single Theme Enlarged":
        # Single theme selector
        selected_theme = st.selectbox(
            "Select a theme:",
            THEME_ORDER
        )

        articles = themed_articles.get(selected_theme, [])
        theme_color = THEME_COLORS.get(selected_theme, '#1f77b4')

        if not articles:
            st.warning(f"No articles found for {selected_theme}.")
        else:
            # Generate word cloud (returns PNG bytes)
            st.markdown(f"### {selected_theme}")
            img = generate_wordcloud(selected_theme, articles)
            if img:
                st.image(img, use_container_width=True)
            else:
                st.warning("Unable to generate word cloud for this theme.")

            st.markdown("---")

            # Top words chart (returns PNG bytes)
            top_words = get_top_words_for_theme(selected_theme, articles, 20)

            if top_words:
                st.subheader(f"Top 20 Trending Words: {selected_theme}")
                chart_img = create_word_frequency_chart(top_words, selected_theme)
                if chart_img:
                    st.image(chart_img, use_container_width=True)
            else:
                st.info("No trending words found.")

    else:
        # All word clouds in 2-column grid
        st.subheader("📊 All Theme Word Clouds")

        for i in range(0, len(THEME_ORDER), 2):
            col1, col2 = st.columns(2)

            # First theme
            theme1 = THEME_ORDER[i]
            articles1 = themed_articles.get(theme1, [])

            with col1:
                st.markdown(f"**{theme1}** ({len(articles1)} articles)")
                if articles1:
                    img1 = generate_wordcloud(theme1, articles1)
                    if img1:
                        st.image(img1, use_container_width=True)
                else:
                    st.info("No articles")

            # Second theme
            if i + 1 < len(THEME_ORDER):
                theme2 = THEME_ORDER[i + 1]
                articles2 = themed_articles.get(theme2, [])

                with col2:
                    st.markdown(f"**{theme2}** ({len(articles2)} articles)")
                    if articles2:
                        img2 = generate_wordcloud(theme2, articles2)
                        if img2:
                            st.image(img2, use_container_width=True)
                    else:
                        st.info("No articles")

            st.markdown("---")

        # Top words for each theme
        st.subheader("📈 Top 20 Keywords by Theme")

        theme_selector = st.selectbox(
            "Select theme for detailed keywords:",
            THEME_ORDER
        )

        articles = themed_articles.get(theme_selector, [])
        top_words = get_top_words_for_theme(theme_selector, articles, 20)

        if top_words:
            chart_img = create_word_frequency_chart(top_words, theme_selector)
            if chart_img:
                st.image(chart_img, use_container_width=True)

            # Also show as table
            st.markdown("#### Top Keywords Table")
            df_words = pd.DataFrame(top_words, columns=['Keyword', 'Frequency'])
            st.dataframe(
                df_words,
                column_config={
                    "Keyword": "Word",
                    "Frequency": st.column_config.ProgressColumn("Frequency", format="%d", min_value=0, max_value=top_words[0][1])
                },
                hide_index=True,
                use_container_width=True
            )
        else:
            st.info("No keywords found.")

    st.markdown("---")

    # Navigation
    st.markdown("### 🔗 Quick Navigation")
    col1, col2, col3 = st.columns(3)

    with col1:
        st.page_link("app.py", label="Back to Dashboard", icon="🏠")

    with col2:
        st.page_link("pages/1_Overview.py", label="Overview", icon="📋")

    with col3:
        st.page_link("pages/2_Deep_Dive.py", label="Deep Dive", icon="🔍")


if __name__ == "__main__":
    main()
