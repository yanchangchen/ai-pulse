"""
Shared sidebar navigation component for AI Pulse.
Provides a consistent sidebar with page links and background status across all pages.

NOTE: app.py builds its own sidebar and duplicates this list verbatim.  When
you add a new page, update both render_sidebar_nav() AND the manual list in
app.py::main()'s ``with st.sidebar:`` block (look for the "Navigation"
subheader and the Quick Navigation grid near the bottom of the main content).
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
        st.page_link("pages/8_Quality_Evaluation.py", label="Quality Evaluation", icon="🔬")

        # Background status tracker
        render_sidebar_info()
