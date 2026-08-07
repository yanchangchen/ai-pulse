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
from core.design_system import apply_design_system, get_plotly_theme_layout
from core.shared_sidebar import render_sidebar_nav
from core.bg_refresher import check_and_show_bg_status

# Page configuration
st.set_page_config(
    page_title="Keyword Analysis - AI Pulse",
    page_icon="🔑",
    layout="wide"
)

# Apply central design system
apply_design_system()


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
        theme_kws = THEMES.get(theme_name, {}).get("keywords", {})
        sorted_kws = sorted(theme_kws.items(), key=lambda kv: (-kv[1], kv[0]))
        return [w[0] for w in sorted_kws[:10]]
    else:
        all_articles = []
        for arts in themed_articles.values():
            all_articles.extend(arts)
        if all_articles:
            top_tuples = get_top_words_for_theme("All", all_articles, 10)
            if top_tuples:
                return [w[0] for w in top_tuples[:10]]
        return ["mcp", "agent", "rag", "blackwell", "gpu", "benchmark", "model", "workflow", "security", "eval"]


def load_keyword_data(supabase, selected_keywords, theme_filter=None, history=None):
    """Load keyword counts per run, combining singular/plural variants."""
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

    if not history:
        history = load_full_history()

    if not history:
        return pd.DataFrame()

    keyword_data = []
    for ts, entry in history.items():
        date = entry.get("date", ts[:10])
        full_text = ""
        articles = entry.get("full_articles", [])
        for a in articles:
            full_text += " " + a.get("title", "") + " " + a.get("summary", "")

        from core.visualiser import canonicalize_word
        text_words = [canonicalize_word(w) for w in full_text.lower().split()]

        row = {"timestamp": ts, "date": date}
        for kw in selected_keywords:
            kw_can = canonicalize_word(kw)
            row[kw] = text_words.count(kw_can)
        keyword_data.append(row)

    df_keywords = pd.DataFrame(keyword_data)
    if not df_keywords.empty:
        df_keywords = df_keywords.sort_values("timestamp")
    return df_keywords


def main() -> None:
    """Main keyword analysis page."""
    themed_articles = get_session_data()

    check_and_show_bg_status()
    render_sidebar_nav()

    supabase = get_supabase_manager()
    history = load_full_history()

    # Header
    st.title("🔑 Keyword Analysis")
    st.markdown("### Keyword Velocity Analytics, Topic Word Clouds & Signal Frequency")
    st.divider()

    # 1. Keyword Velocity Analytics
    col_v_head, col_v_reset = st.columns([4, 1])
    with col_v_head:
        st.subheader("🚀 Keyword Velocity Analytics")
        st.caption("Track the mention frequency and velocity of top AI keywords over time")
    with col_v_reset:
        if st.button("🔄 Reset Filters", key="btn_reset_kw_filters"):
            if "_prev_kw_theme_filter" in st.session_state:
                del st.session_state["_prev_kw_theme_filter"]
            if "kw_velocity_multiselect" in st.session_state:
                del st.session_state["kw_velocity_multiselect"]
            st.rerun()

    col_filter, col_custom = st.columns([1, 1])

    with col_filter:
        theme_filter = st.selectbox(
            "Filter Keywords by Theme:",
            options=["All Themes (No Filter)"] + THEME_ORDER,
            key="kw_velocity_theme_filter"
        )

    top_10_kws = get_top_10_keywords_for_theme(theme_filter, themed_articles)

    last_theme = st.session_state.get("_prev_kw_theme_filter", None)
    if last_theme != theme_filter:
        st.session_state["_prev_kw_theme_filter"] = theme_filter
        st.session_state["kw_velocity_multiselect"] = top_10_kws

    with col_custom:
        custom_kws = st.text_input(
            "💡 Add custom keywords (comma-separated):",
            value="",
            key="kw_velocity_custom_kws"
        )

    current_selected = st.session_state.get("kw_velocity_multiselect", top_10_kws)
    options_list = list(dict.fromkeys(top_10_kws + current_selected))
    if custom_kws:
        for kw in custom_kws.split(","):
            kw_clean = kw.strip().lower()
            if kw_clean and kw_clean not in options_list:
                options_list.append(kw_clean)

    selected_keywords = st.multiselect(
        "Select Keywords to Visualise:",
        options=options_list,
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
            # Apply adaptive layout (no hardcoded dark mode contrast clash)
            theme_layout = get_plotly_theme_layout()
            theme_layout.update({
                "xaxis_title": "Run Date",
                "yaxis_title": "Mention Count",
                "legend": dict(
                    orientation="h",
                    yanchor="top",
                    y=-0.25,
                    xanchor="center",
                    x=0.5
                ),
                "margin": dict(l=0, r=0, t=40, b=60)
            })
            fig_kw.update_layout(**theme_layout)
            st.plotly_chart(fig_kw, width="stretch")
        else:
            st.info("No matching mentions found for the selected keywords across runs.")
    else:
        st.warning("Please select or configure at least one keyword.")

    st.divider()

    # 2. Word Clouds & Frequency Analysis
    st.subheader("☁️ Theme Word Clouds & Frequency Distributions")

    view_mode = st.radio(
        "Visualisation View",
        ["Word Clouds", "Top Frequency Bar Charts"],
        horizontal=True
    )

    if view_mode == "Word Clouds":
        st.markdown("#### ☁️ Theme Word Clouds")

        theme_options = ["All Themes"] + THEME_ORDER
        selected_cloud_theme = st.selectbox("Select Theme for Word Cloud", theme_options)

        if selected_cloud_theme == "All Themes":
            all_articles = []
            for articles_list in themed_articles.values():
                all_articles.extend(articles_list)

            if all_articles:
                img_buf = generate_wordcloud("All Themes", all_articles)
                if img_buf:
                    st.image(img_buf, width="stretch")
                else:
                    st.warning("Could not generate word cloud for All Themes.")
            else:
                st.info("No articles available for word cloud generation.")
        else:
            articles = themed_articles.get(selected_cloud_theme, [])
            if articles:
                img_buf = generate_wordcloud(selected_cloud_theme, articles)
                if img_buf:
                    st.image(img_buf, width="stretch")
                else:
                    st.warning(f"Could not generate word cloud for {selected_cloud_theme}.")
            else:
                st.info(f"No articles available for {selected_cloud_theme}.")

        st.divider()

        with st.expander("🖼️ View All 7 Theme Word Clouds Side-by-Side"):
            row1_col1, row1_col2 = st.columns(2)

            for i, theme in enumerate(THEME_ORDER):
                target_col = row1_col1 if i % 2 == 0 else row1_col2
                articles = themed_articles.get(theme, [])

                with target_col:
                    st.markdown(f"##### {theme}")
                    if articles:
                        img_buf = generate_wordcloud(theme, articles)
                        if img_buf:
                            st.image(img_buf, width="stretch")
                        else:
                            st.caption("Insufficient text for word cloud.")
                    else:
                        st.caption("No articles available.")

    else:
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


if __name__ == "__main__":
    main()
