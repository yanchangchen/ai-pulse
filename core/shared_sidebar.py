"""
Shared sidebar navigation component for AI Pulse.
Provides a consistent sidebar with grouped page links, an active-page
marker, and background status across all pages.
"""

import streamlit as st

# (group caption, [(page path, label, icon, nav key)]) — group "" renders
# without a caption. Keys are what pages pass as render_sidebar_nav(current=...).
_NAV_GROUPS = [
    ("", [
        ("app.py", "Home", "🏠", "home"),
    ]),
    ("Daily intelligence", [
        ("pages/1_Overview.py", "Overview", "📋", "overview"),
        ("pages/2_Deep_Dive.py", "Deep Dive", "🔍", "deep_dive"),
        ("pages/3_Keyword_Analysis.py", "Keyword Analysis", "🔑", "keywords"),
        ("pages/6_Trend_Analytics.py", "Trend Analytics", "📈", "trends"),
        ("pages/4_Sources.py", "Sources", "📰", "sources"),
    ]),
    ("Sage & memory", [
        ("pages/5_History.py", "Memory Wiki · Ask Sage", "🧠", "memory"),
    ]),
    ("Quality & evidence", [
        ("pages/7_Quality_Evaluation.py", "Quality Evaluation", "🔬", "quality"),
        ("pages/9_Eval_Sets.py", "Eval Sets", "🧪", "eval_sets"),
    ]),
    ("Project", [
        ("pages/8_Feedback_&_Roadmap.py", "Feedback & Roadmap", "💡", "feedback"),
        ("pages/10_Blog.py", "Dev Blog", "📓", "blog"),
    ]),
]


def render_sidebar_nav(current: str = "") -> None:
    """Render the standard sidebar navigation used on every page.

    ``current`` is the calling page's nav key (see ``_NAV_GROUPS``); the
    matching link renders bold with a ▸ marker so the reader always knows
    where they are. Omitting it keeps every link unmarked (backwards
    compatible).
    """
    from core.bg_refresher import render_sidebar_info

    with st.sidebar:
        st.header("🧭 Navigation")
        for group, items in _NAV_GROUPS:
            if group:
                st.caption(group)
            for path, label, icon, key in items:
                active = bool(current) and key == current
                st.page_link(
                    path,
                    label=f"**▸ {label}**" if active else label,
                    icon=icon,
                )

        # Engine Settings & Mode Selector — a power-user control, collapsed
        # so the sidebar stays focused on navigation + background status.
        st.divider()
        with st.expander("⚙️ Engine Settings", expanded=False):
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