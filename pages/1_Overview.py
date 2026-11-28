"""
AI Pulse - Overview Page
Theme cards for the LATEST intelligence run: each brief is the run's
synthesis (newly fetched articles + backlog drained this run), with
coverage stated explicitly — including the zero state.
"""

import streamlit as st

from config.settings import DAYS_LOOKBACK
from config.themes import THEME_ORDER, THEME_COLORS
from core.design_system import apply_design_system, sanitize_summary_html, format_display_timestamp
from core.shared_sidebar import render_sidebar_nav
from core.bg_refresher import check_and_show_bg_status
from core.history_manager import compute_run_processing_stats, theme_coverage_counts
from core.provenance import render_provenance_chip, strip_fallback_banner
from core.supabase_client import get_supabase_manager

# Page configuration
st.set_page_config(
    page_title="Overview - AI Pulse",
    page_icon="⚡",
    layout="wide"
)

apply_design_system()


def get_session_data():
    """Get data from session state."""
    if 'themed_articles' not in st.session_state or 'summaries' not in st.session_state:
        st.switch_page("app.py")
        st.stop()

    return st.session_state.themed_articles, st.session_state.summaries


def _latest_run_label() -> str:
    """Best-effort timestamp of the run being displayed (cloud-first)."""
    try:
        supabase = get_supabase_manager()
        if supabase.is_available():
            runs = supabase.get_all_runs(limit=1)
            if runs:
                return format_display_timestamp(runs[0].get("run_timestamp", ""))
    except Exception:
        pass
    try:
        from core.history_manager import load_full_history
        history = load_full_history()
        if history:
            return format_display_timestamp(sorted(history.keys(), reverse=True)[0])
    except Exception:
        pass
    return "latest cached run"


def _render_theme_card(col, theme, summary, articles):
    """One theme card: coverage meta (always stated), provenance, signal."""
    color = THEME_COLORS.get(theme, '#666')
    cov = theme_coverage_counts(summary, fallback_total=len(articles))
    if cov["total"] > 0:
        meta = (
            f"📰 <b>{cov['included']} of {cov['total']}</b> reflected"
            f" · ⏳ {cov['waiting']} waiting"
        )
    else:
        meta = f"📰 <b>{len(articles)}</b> articles tracked"

    with col:
        st.markdown(f"""
        <div class="theme-card" style="border-top: 3px solid {color} !important;">
            <div class="card-title" style="color: {color};">{theme}</div>
            <div class="card-meta">{meta}</div>
            {render_provenance_chip(summary)}
            <div style="margin-top: 14px;">
                <div class="card-section-label" style="color: {color};">THE LATEST SIGNAL</div>
                <div style="font-size: 15px; line-height: 1.6;">{sanitize_summary_html(strip_fallback_banner(summary.get('what_is_happening', 'No signal available.')))}</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        with st.expander("🎯 Strategic Significance"):
            st.markdown(summary.get('why_it_matters', 'No analysis available.'))

        with st.expander("👁️ Future Outlook (Watchlist)"):
            st.markdown(summary.get('what_to_watch', 'No items to watch.'))


def main() -> None:
    """Main overview page."""
    themed_articles, summaries = get_session_data()

    check_and_show_bg_status()
    render_sidebar_nav(current="overview")

    st.title("📋 Theme Overview")
    st.markdown(f"### Latest intelligence run — {_latest_run_label()}")
    st.caption(
        f"Each brief synthesises the articles selected THIS run — newly fetched "
        f"plus backlog drained from earlier runs. Sources are scanned across the "
        f"past {DAYS_LOOKBACK} days; every article is synthesised once, then carried "
        f"in the archive."
    )

    # Run-level processing strip (same machinery as the Memory Timeline).
    rs = compute_run_processing_stats(summaries)
    if rs["new_unprocessed"] or rs["processed"]:
        c1, c2, c3 = st.columns(3)
        c1.metric("🆕 New articles this run", rs["new_unprocessed"])
        c2.metric("✅ Synthesised", rs["processed"])
        c3.metric("⏳ Waiting for next run", rs["waiting"])
    st.divider()

    # Theme cards in 2-column grid
    for i in range(0, len(THEME_ORDER), 2):
        col1, col2 = st.columns(2)
        theme1 = THEME_ORDER[i]
        _render_theme_card(col1, theme1, summaries.get(theme1, {}), themed_articles.get(theme1, []))
        if i + 1 < len(THEME_ORDER):
            theme2 = THEME_ORDER[i + 1]
            _render_theme_card(col2, theme2, summaries.get(theme2, {}), themed_articles.get(theme2, []))
        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()