"""
Shared sidebar navigation component for AI Pulse.
Provides a consistent sidebar with page links and background status across all pages.
"""

import streamlit as st


def render_sidebar_nav() -> None:
    """Render the standard sidebar navigation used on every page."""
    from core.bg_refresher import render_sidebar_info

    with st.sidebar:
        st.header("🧭 Navigation")
        st.page_link("app.py", label="Home", icon="🏠")
        st.page_link("pages/1_Overview.py", label="Overview", icon="📋")
        st.page_link("pages/2_Deep_Dive.py", label="Deep Dive", icon="🔍")
        st.page_link("pages/3_Keyword_Analysis.py", label="Keyword Analysis", icon="🔑")
        st.page_link("pages/4_Sources.py", label="Sources", icon="📰")
        st.page_link("pages/5_History.py", label="Memory Wiki", icon="🧠")
        st.page_link("pages/6_Trend_Analytics.py", label="Trend Analytics", icon="📈")
        st.page_link("pages/7_Quality_Evaluation.py", label="Quality Evaluation", icon="🔬")
        st.page_link("pages/8_Feedback_&_Roadmap.py", label="Feedback & Roadmap", icon="💡")

        # Engine Settings & Mode Selector
        st.divider()
        st.subheader("⚙️ Engine Settings")
        mode_options = [
            "🤖 Hybrid Auto-Fallback",
            "⚡ Non-LLM Extractive Only",
            "🧠 Live LLM Strict"
        ]
        current_mode = st.session_state.get("summariser_mode", "🤖 Hybrid Auto-Fallback")
        idx = mode_options.index(current_mode) if current_mode in mode_options else 0
        selected_mode = st.selectbox(
            "Summarisation Engine:",
            options=mode_options,
            index=idx,
            key="sb_summariser_mode",
            help="Select whether to use LLM synthesis with automatic non-LLM fallback, force instant non-LLM LexRank/Luhn extraction, or require live LLM."
        )
        st.session_state["summariser_mode"] = selected_mode

        # Background status tracker
        render_sidebar_info()
