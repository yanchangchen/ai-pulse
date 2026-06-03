"""
AI Pulse - History & Wiki Page
View past runs and track the evolution of AI developments over time.
"""

import streamlit as st
from datetime import datetime
from core.history_manager import load_full_history
from config.themes import THEME_ORDER, THEME_COLORS

# Page configuration
st.set_page_config(
    page_title="Memory Wiki - AI Pulse",
    page_icon="🧠",
    layout="wide"
)

# Custom CSS for the Wiki look
st.markdown("""
<style>
    .wiki-entry {
        background-color: #ffffff;
        padding: 25px;
        border-radius: 12px;
        border: 1px solid #e0e0e0;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
        margin-bottom: 30px;
    }
    .wiki-date {
        font-size: 24px;
        font-weight: bold;
        color: #1a1a1a;
        margin-bottom: 10px;
        border-bottom: 2px solid #f0f0f0;
        padding-bottom: 5px;
    }
    .theme-pill {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 15px;
        font-size: 12px;
        font-weight: bold;
        margin-right: 5px;
        color: white;
    }
    .wiki-section-header {
        font-weight: bold;
        color: #444;
        margin-top: 15px;
        font-size: 14px;
        text-transform: uppercase;
        letter-spacing: 1px;
    }
</style>
""", unsafe_allow_html=True)

def main() -> None:
    from core.bg_refresher import check_and_show_bg_status, render_sidebar_info

    # 1. Top of page alert if background update finished
    check_and_show_bg_status()

    st.title("🧠 Memory Wiki")
    st.markdown("### Historical AI Intelligence Timeline")
    st.info("This page tracks the evolution of AI trends across every 'Refresh' you perform.")

    history = load_full_history()

    if not history:
        st.warning("No historical data found. Run a data refresh on the main dashboard to start building your wiki.")
        if st.page_link("app.py", label="Back to Dashboard", icon="🏠"):
            pass
        return

    # Sort history by timestamp descending
    sorted_timestamps = sorted(history.keys(), reverse=True)

    # Sidebar Filter
    with st.sidebar:
        st.header("🧭 Navigation")
        st.page_link("app.py", label="Home", icon="🏠")
        st.page_link("pages/1_Overview.py", label="Overview", icon="📋")
        st.page_link("pages/2_Deep_Dive.py", label="Deep Dive", icon="🔍")
        st.page_link("pages/3_Word_Clouds.py", label="Word Clouds", icon="☁️")
        st.page_link("pages/4_Sources.py", label="Sources", icon="📰")
        st.page_link("pages/5_History.py", label="Memory Wiki", icon="🧠")
        st.page_link("pages/6_Trend_Analytics.py", label="Trend Analytics", icon="📈")
        st.page_link("pages/7_Emerging_Trends.py", label="Emerging Trends", icon="🚀")

        # Background status tracker inside the sidebar
        render_sidebar_info()
        st.divider()
        st.header("🔍 Filter History")
        selected_theme = st.selectbox("Filter by Theme", ["All Themes"] + THEME_ORDER)
        st.divider()
        st.caption(f"Total recorded runs: {len(history)}")

    # Display entries
    for ts in sorted_timestamps:
        entry = history[ts]
        date = entry["date"]
        summaries = entry["summaries"]
        counts = entry.get("counts", {})

        # If filtering by theme, check if it exists in this entry
        if selected_theme != "All Themes" and selected_theme not in summaries:
            continue

        with st.container():
            st.markdown(f'<div class="wiki-date">📅 {ts}</div>', unsafe_allow_html=True)
            
            themes_to_show = THEME_ORDER if selected_theme == "All Themes" else [selected_theme]
            
            cols = st.columns(len(themes_to_show) if len(themes_to_show) < 3 else 2)
            
            for i, theme in enumerate(themes_to_show):
                if theme in summaries:
                    summary = summaries[theme]
                    color = THEME_COLORS.get(theme, "#666")
                    count = counts.get(theme, 0)
                    
                    with (cols[i % len(cols)] if len(themes_to_show) > 1 else st.container()):
                        # Theme Header with Pill
                        st.markdown(f"""
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                            <span class="theme-pill" style="background-color: {color};">{theme}</span>
                            <span style="font-size: 12px; color: #888;">📰 {count} articles</span>
                        </div>
                        """, unsafe_allow_html=True)
                        
                        # Content using native Streamlit for better readability and rendering
                        st.markdown("**WHAT HAPPENED**")
                        st.write(summary.get('what_is_happening', 'No data.'))
                        
                        st.markdown("**SIGNIFICANCE**")
                        st.write(summary.get('why_it_matters', 'No analysis.'))
                        
                        st.markdown("**WATCHLIST**")
                        st.write(summary.get('what_to_watch', 'No items.'))
                        
                        st.divider()

    st.markdown("---")
    st.page_link("app.py", label="Back to Dashboard", icon="🏠")

if __name__ == "__main__":
    main()
