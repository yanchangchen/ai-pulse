"""
AI Pulse - Keyword Analysis Page
Visualises keyword velocity trends, theme word clouds, and frequency distributions.
"""

import streamlit as st
import pandas as pd
import plotly.express as px

from config.themes import THEMES, THEME_ORDER, THEME_COLORS
from core.visualiser import (
    generate_wordcloud,
    get_top_words_for_theme,
    create_word_frequency_chart
)
from core.history_manager import load_full_history
from core.supabase_client import get_supabase_manager

# Page configuration
st.set_page_config(
    page_title="Keyword Analysis - AI Pulse",
    page_icon="🔑",
    layout="wide"
)


def get_session_data():
    """Get data from session state."""
    if 'themed_articles' not in st.session_state:
        st.switch_page("app.py")
        st.stop()

    return st.session_state.themed_articles


def get_top_10_keywords_for_theme(theme_name: str, themed_articles: dict) -> list:
    """Get the top 10 high-signal keywords for a given theme (or all themes)."""
    if theme_name != "All Themes (No Filter)":
        articles = themed_articles.get(theme_name, [])
        if articles:
            top_tuples = get_top_words_for_theme(theme_name, articles, 10)
            if top_tuples:
                return [w[0] for w in top_tuples[:10]]
        # Fallback to configured theme keywords in THEMES
        theme_kws = THEMES.get(theme_name, {}).get("keywords", {})
        sorted_kws = sorted(theme_kws.items(), key=lambda kv: (-kv[1], kv[0]))
        return [w[0] for w in sorted_kws[:10]]
    else:
        # Global top 10 keywords across all themes
        all_articles = []
        for arts in themed_articles.values():
            all_articles.extend(arts)
        if all_articles:
            top_tuples = get_top_words_for_theme("All", all_articles, 10)
            if top_tuples:
                return [w[0] for w in top_tuples[:10]]
        return ["mcp", "agent", "rag", "blackwell", "gpu", "benchmark", "model", "workflow", "security", "eval"]


def load_keyword_data(supabase, selected_keywords, theme_filter=None, history=None):
    """Load keyword counts per run, combining singular/plural variants (e.g. agent + agents)."""
    if not selected_keywords:
        return pd.DataFrame()

    if supabase and supabase.is_available():
        keyword_list = supabase.get_keyword_velocity(selected_keywords)
        if keyword_list:
            kw_runs = {}
            for item in keyword_list:
                ts = item["run_timestamp"]
                dt = item["run_date"]
                kw = item["keyword"]
                cnt = item["count"]
                if ts not in kw_runs:
                    kw_runs[ts] = {"timestamp": ts, "date": dt}
                kw_runs[ts][kw] = cnt

            kw_data = []
            for ts, row in kw_runs.items():
                for kw in selected_keywords:
                    if kw not in row:
                        row[kw] = 0
                kw_data.append(row)
            df_keywords = pd.DataFrame(kw_data)
            if not df_keywords.empty:
                df_keywords = df_keywords.sort_values("timestamp")
            return df_keywords

    # Local fallback
    if not history:
        history = load_full_history()

    if not history:
        return pd.DataFrame()

    keyword_data = []
    for ts, entry in history.items():
        date = entry.get("date", ts[:10])
        full_text = ""
        articles = entry.get("full_articles", [])
        if theme_filter and theme_filter != "All Themes (No Filter)":
            articles = [a for a in articles if a.get("theme") == theme_filter or a.get("category") == theme_filter]

        for article in articles:
            full_text += f" {article.get('title', '')} {article.get('summary', '')}"
        full_text = full_text.lower()

        kw_row = {"timestamp": ts, "date": date}
        for kw in selected_keywords:
            kw_lower = kw.lower().strip()
            c_kw = canonicalize_word(kw_lower)
            # Count exact keyword + variant singular/plural
            cnt = full_text.count(kw_lower)
            if c_kw == kw_lower and not kw_lower.endswith("s"):
                cnt += full_text.count(kw_lower + "s")
            elif kw_lower.endswith("s") and len(kw_lower) > 3:
                cnt += full_text.count(kw_lower[:-1])
            kw_row[kw] = cnt
        keyword_data.append(kw_row)

    df_keywords = pd.DataFrame(keyword_data)
    if not df_keywords.empty:
        df_keywords = df_keywords.sort_values("timestamp")
    return df_keywords


def main() -> None:
    """Main keyword analysis page."""
    themed_articles = get_session_data()

    from core.bg_refresher import check_and_show_bg_status
    from core.shared_sidebar import render_sidebar_nav

    check_and_show_bg_status()
    render_sidebar_nav()

    supabase = get_supabase_manager()
    history = load_full_history()

    # Page Header
    st.title("🔑 Keyword Analysis")
    st.markdown("### Keyword Velocity Analytics, Topic Word Clouds & Signal Frequency")
    st.markdown("---")

    # ---------------------------------------------------------------------------
    # 1. FIRST GRAPH: Keyword Velocity Analytics
    # ---------------------------------------------------------------------------
    st.subheader("🚀 Keyword Velocity Analytics")
    st.caption("Track the mention frequency and velocity of top AI keywords over time")

    col_filter, col_custom = st.columns([1, 1])

    with col_filter:
        theme_filter = st.selectbox(
            "Filter Keywords by Theme:",
            options=["All Themes (No Filter)"] + THEME_ORDER,
            key="kw_velocity_theme_filter"
        )

    # Derive top 10 keywords for the selected theme filter
    top_10_kws = get_top_10_keywords_for_theme(theme_filter, themed_articles)

    with col_custom:
        custom_kws = st.text_input(
            "💡 Add custom keywords (comma-separated):",
            value="",
            key="kw_velocity_custom_kws"
        )

    options_list = list(top_10_kws)
    if custom_kws:
        for kw in custom_kws.split(","):
            kw_clean = kw.strip().lower()
            if kw_clean and kw_clean not in options_list:
                options_list.append(kw_clean)

    selected_keywords = st.multiselect(
        "Select Keywords to Visualise:",
        options=options_list,
        default=top_10_kws,
        key="kw_velocity_multiselect"
    )

    if selected_keywords:
        with st.spinner("Calculating keyword velocity trajectories..."):
            df_keywords = load_keyword_data(supabase, selected_keywords, theme_filter=theme_filter, history=history)

        if not df_keywords.empty and any(kw in df_keywords.columns for kw in selected_keywords):
            available_kw = [kw for kw in selected_keywords if kw in df_keywords.columns]
            df_melted_kw = df_keywords.melt(id_vars=["date"], value_vars=available_kw, var_name="Keyword", value_name="Mentions")

            fig_kw = px.line(
                df_melted_kw,
                x="date",
                y="Mentions",
                color="Keyword",
                markers=True,
                title=f"Keyword Velocity — {theme_filter}"
            )
            fig_kw.update_layout(
                template="plotly_dark",
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                xaxis_title="Run Date",
                yaxis_title="Mention Count",
                legend=dict(
                    orientation="h",
                    yanchor="top",
                    y=-0.25,
                    xanchor="center",
                    x=0.5
                ),
                margin=dict(l=0, r=0, t=40, b=60)
            )
            st.plotly_chart(fig_kw, width="stretch")
        else:
            st.info("No matching mentions found for the selected keywords across runs.")
    else:
        st.warning("Please select or configure at least one keyword.")

    st.markdown("---")

    # ---------------------------------------------------------------------------
    # 2. Word Clouds & Frequency Analysis
    # ---------------------------------------------------------------------------
    st.subheader("☁️ Theme Word Clouds & Frequency Distributions")

    view_mode = st.radio(
        "View Mode:",
        ["All Word Clouds", "Single Theme Enlarged"],
        horizontal=True
    )

    if view_mode == "Single Theme Enlarged":
        selected_theme = st.selectbox(
            "Select a theme:",
            THEME_ORDER
        )

        articles = themed_articles.get(selected_theme, [])

        if not articles:
            st.warning(f"No articles found for {selected_theme}.")
        else:
            st.markdown(f"### {selected_theme}")
            img = generate_wordcloud(selected_theme, articles)
            if img:
                st.image(img, width="stretch")
            else:
                st.warning("Unable to generate word cloud for this theme.")

            st.markdown("---")

            top_words = get_top_words_for_theme(selected_theme, articles, 20)
            if top_words:
                st.subheader(f"Top 20 Trending Words: {selected_theme}")
                chart_img = create_word_frequency_chart(top_words, selected_theme)
                if chart_img:
                    st.image(chart_img, width="stretch")
            else:
                st.info("No trending words found.")

    else:
        st.markdown("#### 📊 All Theme Word Clouds")

        for i in range(0, len(THEME_ORDER), 2):
            col1, col2 = st.columns(2)

            theme1 = THEME_ORDER[i]
            articles1 = themed_articles.get(theme1, [])

            with col1:
                st.markdown(f"**{theme1}** ({len(articles1)} articles)")
                if articles1:
                    img1 = generate_wordcloud(theme1, articles1)
                    if img1:
                        st.image(img1, width="stretch")
                else:
                    st.info("No articles")

            if i + 1 < len(THEME_ORDER):
                theme2 = THEME_ORDER[i + 1]
                articles2 = themed_articles.get(theme2, [])

                with col2:
                    st.markdown(f"**{theme2}** ({len(articles2)} articles)")
                    if articles2:
                        img2 = generate_wordcloud(theme2, articles2)
                        if img2:
                            st.image(img2, width="stretch")
                    else:
                        st.info("No articles")

            st.markdown("---")

        st.markdown("#### 📈 Top 20 Keywords by Theme")

        theme_selector = st.selectbox(
            "Select theme for detailed keywords:",
            THEME_ORDER
        )

        articles = themed_articles.get(theme_selector, [])
        top_words = get_top_words_for_theme(theme_selector, articles, 20)

        if top_words:
            chart_img = create_word_frequency_chart(top_words, theme_selector)
            if chart_img:
                st.image(chart_img, width="stretch")

            st.markdown("##### Top Keywords Table")
            df_words = pd.DataFrame(top_words, columns=['Keyword', 'Frequency'])
            st.dataframe(
                df_words,
                column_config={
                    "Keyword": "Word",
                    "Frequency": st.column_config.ProgressColumn("Frequency", format="%d", min_value=0, max_value=top_words[0][1])
                },
                hide_index=True,
                width="stretch"
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

