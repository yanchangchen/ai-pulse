"""
AI Pulse - Deep Dive Page
Detailed analysis and article list for each thematic area.
"""

import streamlit as st
import pandas as pd

from config.themes import THEME_ORDER, THEME_COLORS
from core.design_system import apply_design_system
from core.shared_sidebar import render_sidebar_nav
from core.bg_refresher import check_and_show_bg_status

# Page configuration
st.set_page_config(
    page_title="Deep Dive - AI Pulse",
    page_icon="🔍",
    layout="wide"
)

# Apply central design system
apply_design_system()


def get_session_data():
    """Get data from session state."""
    if 'themed_articles' not in st.session_state or 'summaries' not in st.session_state:
        st.switch_page("app.py")
        st.stop()

    return st.session_state.themed_articles, st.session_state.summaries


def main() -> None:
    """Main deep dive page."""
    themed_articles, summaries = get_session_data()

    # Top of page alert if background update finished
    check_and_show_bg_status()

    # Sidebar Navigation
    render_sidebar_nav()

    # Header
    st.title("🔍 Thematic Deep Dive")
    st.markdown("### Granular Analysis & Article Explorer")
    st.divider()

    # Theme selector
    col_sel, col_export = st.columns([3, 1])
    with col_sel:
        selected_theme = st.selectbox(
            "Select Strategic Theme",
            THEME_ORDER,
            index=0,
            key="deep_dive_theme_select"
        )
    with col_export:
        # Multi-theme export support
        all_export_rows = []
        for th, arts in themed_articles.items():
            for a in arts:
                all_export_rows.append({
                    "Theme": th,
                    "Title": a.get("title", ""),
                    "Source": a.get("source", ""),
                    "URL": a.get("link", ""),
                    "Published": a.get("published", "")
                })
        if all_export_rows:
            df_all = pd.DataFrame(all_export_rows)
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            st.download_button(
                "📥 Export All Themes CSV",
                df_all.to_csv(index=False).encode("utf-8"),
                f"ai_pulse_all_themes.csv",
                "text/csv",
                key="btn_export_all_csv",
                help="Export all tracked articles across all themes as CSV."
            )

    theme_color = THEME_COLORS.get(selected_theme, '#1f77b4')
    theme_summary = summaries.get(selected_theme, {})
    theme_articles = themed_articles.get(selected_theme, [])

    if len(theme_articles) < 3:
        st.warning(f"⚠️ Limited coverage this period with only {len(theme_articles)} articles for this theme.")

    st.divider()

    # Summary header tile
    col_header, col_count = st.columns([3, 1])
    with col_header:
        st.markdown(f"<h2 style='color: {theme_color}; margin-top:0;'>{selected_theme}</h2>", unsafe_allow_html=True)
    with col_count:
        st.markdown(f"""
        <div class="metric-card" style="text-align: center; border-color: {theme_color}40 !important;">
            <div style="font-size: 30px; font-weight: bold; color: {theme_color};">{len(theme_articles)}</div>
            <div class="card-meta">Articles Tracked</div>
        </div>
        """, unsafe_allow_html=True)

    # What is happening
    st.subheader("📰 The Signal (What is Happening)")
    if theme_summary.get('what_is_happening'):
        st.markdown(theme_summary['what_is_happening'])
    else:
        st.info("No summary available for this theme.")

    st.divider()

    # Strategic Significance & Impact
    st.subheader("🎯 Strategic Significance & Impact")
    has_eng = bool(theme_summary.get('engineering_tradeoffs') and theme_summary['engineering_tradeoffs'] != "No engineering tradeoffs analyzed.")
    has_prod = bool(theme_summary.get('product_impact') and theme_summary['product_impact'] != "No product impact analyzed.")

    if has_eng or has_prod:
        col_eng, col_prod = st.columns(2)
        with col_eng:
            st.markdown("##### 🛠️ Engineering Blueprint")
            st.info(theme_summary.get('engineering_tradeoffs', 'No engineering tradeoffs analyzed.'))
        with col_prod:
            st.markdown("##### 💼 Product Feasibility")
            st.success(theme_summary.get('product_impact', 'No product impact analyzed.'))
    else:
        if theme_summary.get('why_it_matters'):
            st.markdown(theme_summary['why_it_matters'])
        else:
            st.info("No analysis available.")

    st.divider()

    # What to watch
    st.subheader("👁️ Watchlist (Future Outlook)")
    if theme_summary.get('what_to_watch'):
        watch_text = theme_summary['what_to_watch']
        if '\n' in watch_text:
            for line in watch_text.split('\n'):
                if line.strip():
                    st.markdown(f"- {line.strip()}")
        else:
            st.markdown(watch_text)
    else:
        st.info("No items to watch.")

    st.divider()

    # Further reading
    st.subheader("📚 Cited Sources & Further Reading")
    if theme_summary.get('further_reading'):
        st.markdown(theme_summary['further_reading'])
    else:
        st.info("No further reading suggestions available.")

    st.divider()

    # Article table & CSV Download
    st.subheader("📋 Tracked Source Articles")

    if theme_articles:
        df_articles = pd.DataFrame([
            {
                "Title": a.get("title", ""),
                "Source": a.get("source", ""),
                "Published": a.get("published", ""),
                "Link": a.get("link", "")
            }
            for a in theme_articles
        ])

        st.dataframe(
            df_articles,
            column_config={
                "Title": st.column_config.TextColumn("Title", width="medium"),
                "Source": st.column_config.TextColumn("Source", width="small"),
                "Published": st.column_config.TextColumn("Published", width="small"),
                "Link": st.column_config.LinkColumn("Link", width="medium")
            },
            hide_index=True,
            use_container_width=True
        )

        csv = df_articles.to_csv(index=False).encode('utf-8')
        st.download_button(
            label=f"📥 Download {selected_theme} CSV",
            data=csv,
            file_name=f"ai_pulse_{selected_theme.lower().replace(' ', '_')}.csv",
            mime="text/csv",
            key="btn_export_single_csv"
        )
    else:
        st.info("No articles found for this theme.")


if __name__ == "__main__":
    main()
