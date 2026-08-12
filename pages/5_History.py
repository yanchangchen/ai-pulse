"""
AI Pulse - Memory Wiki Page
Chat with Sage, browse the historical timeline, compare runs, and track the evolution of AI developments.
"""

import streamlit as st
from datetime import datetime, timedelta
import pandas as pd

from config.themes import THEME_ORDER, THEME_COLORS
from core.history_manager import load_full_history
from core.shared_sidebar import render_sidebar_nav
from core.supabase_client import get_supabase_manager

from core.design_system import apply_design_system, sanitize_summary_html, format_display_timestamp

# Page configuration
st.set_page_config(
    page_title="Memory Wiki - AI Pulse",
    page_icon="🧠",
    layout="wide"
)

# Apply central design system
apply_design_system()

def main() -> None:
    from core.bg_refresher import check_and_show_bg_status

    # 1. Alert if background update finished
    check_and_show_bg_status()

    st.title("🧠 Memory Wiki")
    st.markdown("### Historical AI Intelligence Archive")
    st.divider()

    # Shared sidebar navigation
    render_sidebar_nav()

    # Initialize Supabase
    supabase = get_supabase_manager()
    using_supabase = supabase.is_available()

    # Gather filter variables in Sidebar
    with st.sidebar:
        st.divider()
        st.header("🔍 Filters")

        # Theme filter
        selected_theme = st.selectbox("Select Theme Filter", ["All Themes"] + THEME_ORDER)

        # Date range filters
        start_date = st.date_input("From Date", value=datetime.now().date() - timedelta(days=90))
        end_date = st.date_input("To Date", value=datetime.now().date())

        # Unique sources filter
        sources = ["All Sources"]
        if using_supabase:
            unique_sources = supabase.get_unique_sources()
            if unique_sources:
                sources.extend(unique_sources)
        selected_source = st.selectbox("Select Source Filter", sources)

    # Construct filter values
    theme_filter_val = None if selected_theme == "All Themes" else selected_theme
    date_from_val = start_date.strftime("%Y-%m-%d")
    date_to_val = (end_date + timedelta(days=1)).strftime("%Y-%m-%d")
    source_filter_val = None if selected_source == "All Sources" else selected_source

    if not using_supabase:
        # Fallback to local history
        st.info("ℹ️ Running in Local Mode: showing latest cached run. Connect Supabase to unlock Sage and full timeline search & comparison.")
        history = load_full_history()

        if not history:
            st.warning("No historical data found. Run a data refresh on the main dashboard to start building your wiki.")
            st.page_link("app.py", label="Back to Dashboard", icon="🏠")
            return

        latest_ts = sorted(history.keys(), reverse=True)[0]
        entry = history[latest_ts]
        summaries = entry.get("summaries", {})
        counts = entry.get("counts", {})

        st.markdown(f'<div class="wiki-date">📅 Latest Cache: {format_display_timestamp(latest_ts)}</div>', unsafe_allow_html=True)

        themes_to_show = THEME_ORDER if selected_theme == "All Themes" else [selected_theme]
        cols = st.columns(min(len(themes_to_show), 2))

        col_idx = 0
        for theme in themes_to_show:
            if theme in summaries:
                summary = _ensure_extractive_summary(summaries[theme], articles=entry.get("themed_articles", {}).get(theme, []))
                color = THEME_COLORS.get(theme, "#666")
                count = counts.get(theme, 0)

                with cols[col_idx % len(cols)]:
                    st.markdown(f"""
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; border-bottom: 1px solid #444; padding-bottom: 5px;">
                        <span class="theme-pill" style="background-color: {color};">{theme}</span>
                        <span style="font-size: 12px; color: #aaa;">📰 {count} articles</span>
                    </div>
                    """, unsafe_allow_html=True)

                    st.markdown("**WHAT HAPPENED**")
                    st.write(summary.get('what_is_happening', 'No data.'))

                    st.markdown("**SIGNIFICANCE**")
                    st.write(summary.get('why_it_matters', 'No analysis.'))

                    st.markdown("**WATCHLIST**")
                    st.write(summary.get('what_to_watch', 'No items.'))
                    st.divider()

                col_idx += 1
        return

    # --- Supabase Powered Mode: 3-tab layout ---
    tab_sage, tab_timeline, tab_compare = st.tabs(["🔮 Ask Sage", "📖 Memory Timeline", "⚖️ Compare Runs"])

    # =========================================================================
    # TAB 1: ASK SAGE
    # =========================================================================
    with tab_sage:
        _render_sage_tab(supabase, theme_filter_val, date_from_val, date_to_val, source_filter_val)

    # =========================================================================
    # TAB 2: MEMORY TIMELINE
    # =========================================================================
    with tab_timeline:
        _render_timeline_tab(supabase, selected_theme, source_filter_val)

    # =========================================================================
    # TAB 3: COMPARE RUNS
    # =========================================================================
    with tab_compare:
        _render_compare_tab(supabase, selected_theme, source_filter_val)


# ---------------------------------------------------------------------------
# Sage chat tab
# ---------------------------------------------------------------------------

def _render_sage_tab(supabase, theme_filter, date_from, date_to, source_filter=None):
    """Render the Ask Sage conversational chat interface."""
    from core.sage_agent import SAGE_INTRO, build_wiki_context, chat_with_sage
    from core.llm_client import LLMClient

    # Sage intro banner
    st.markdown(f"""
    <div class="sage-intro">
        <h3>🔮 Sage — AI Research Analyst</h3>
        <p>"{SAGE_INTRO}"</p>
    </div>
    """, unsafe_allow_html=True)

    # Initialise chat state
    if "sage_messages" not in st.session_state:
        st.session_state.sage_messages = []

    # New conversation button
    col_new, col_info = st.columns([1, 3])
    with col_new:
        if st.button("🔄 New conversation", key="sage_new_convo"):
            st.session_state.sage_messages = []
            st.rerun()
    with col_info:
        theme_label = theme_filter if theme_filter else "All Themes"
        source_label = source_filter if source_filter else "All Sources"
        st.caption(f"📌 Context: **Theme: {theme_label} | Source: {source_label}** | {date_from} → {date_to}")

    st.divider()

    # Render conversation history
    for msg in st.session_state.sage_messages:
        avatar = "🔮" if msg["role"] == "assistant" else "👤"
        with st.chat_message(msg["role"], avatar=avatar):
            st.markdown(msg["content"])

    # Chat input
    user_input = st.chat_input("Ask Sage anything about AI trends...")

    if user_input:
        # Display user message immediately
        with st.chat_message("user", avatar="👤"):
            st.markdown(user_input)
        st.session_state.sage_messages.append({"role": "user", "content": user_input})

        # Build context and get Sage's response
        with st.chat_message("assistant", avatar="🔮"):
            with st.spinner("Sage is analysing the archive..."):
                # Build grounded wiki context
                wiki_context = build_wiki_context(
                    supabase=supabase,
                    question=user_input,
                    theme_filter=theme_filter,
                    date_from=date_from,
                    date_to=date_to,
                    source_filter=source_filter,
                )

                # Call LLM
                llm = LLMClient()
                sage_response = chat_with_sage(
                    llm_client=llm,
                    messages=st.session_state.sage_messages,
                    wiki_context=wiki_context,
                )

            st.markdown(sage_response)

        st.session_state.sage_messages.append({"role": "assistant", "content": sage_response})
        st.rerun()


def _ensure_extractive_summary(s: dict, supabase=None, run_id: str = "", theme: str = "", articles: list = None) -> dict:
    """If ANY historical summary contains legacy quota warning text or older extractive phrasing,
    dynamically generate a fresh non-LLM extractive summary using the per-article LexRank algorithm.
    Applies universally across all runs and themes.
    """
    if not s:
        return s

    text = s.get("what_is_happening", "")
    legacy_indicators = [
        "Ollama Cloud weekly quota limit reached",
        "Unable to generate new summary",
        "Live LLM synthesis paused",
        "quota limit reached",
        "Compiled deterministically using LexRank & Luhn",
        "live LLM quota paused",
        "Extractive summary unavailable",
    ]

    if any(ind.lower() in text.lower() for ind in legacy_indicators):
        if not articles and supabase and run_id and theme:
            articles = supabase.get_articles_for_run(run_id, theme) or []

        if articles:
            from core.summariser import extractive_theme_summary
            info_prefix = "ℹ️ *Non-LLM Extractive Summary: Generated deterministically using lead sentence extraction because live LLM synthesis was paused.*"
            extractive = extractive_theme_summary(theme, articles)
            orig_text = extractive.get("what_is_happening", "")
            extractive["what_is_happening"] = f"{info_prefix}\n\n{orig_text}"
            return extractive

    return s


# ---------------------------------------------------------------------------
# Timeline tab
# ---------------------------------------------------------------------------

def _render_timeline_tab(supabase, selected_theme, source_filter_val):
    """Render the existing Memory Timeline browser."""
    total_runs = supabase.get_total_run_count()

    if total_runs == 0:
        st.warning("No runs found in Supabase. Run a data refresh on the main dashboard to populate the database.")
        return

    if 'wiki_page_index' not in st.session_state:
        st.session_state.wiki_page_index = 0

    # Ensure page index stays in bounds
    max_pages = max(1, (total_runs + 4) // 5)
    st.session_state.wiki_page_index = min(st.session_state.wiki_page_index, max_pages - 1)

    offset = st.session_state.wiki_page_index * 5
    runs = supabase.get_all_runs(limit=5, offset=offset)

    if runs:
        for run in runs:
            run_id = run["id"]
            run_ts = run["run_timestamp"]
            run_articles_all = supabase.get_articles_for_run(run_id) or []
            total_arts = run.get("total_articles", 0)
            if total_arts == 0:
                total_arts = len(run_articles_all)

            st.markdown(
                f'<div class="wiki-date">📅 Run: {format_display_timestamp(run_ts)} <span style="font-size:14px;font-weight:normal;color:#aaa;">({total_arts} total articles tracked)</span></div>',
                unsafe_allow_html=True
            )

            summaries = supabase.get_summaries_for_run(run_id) or []
            summaries_dict = {s["theme_name"]: s for s in summaries}

            themes_to_show = THEME_ORDER if selected_theme == "All Themes" else [selected_theme]

            for theme in themes_to_show:
                if theme in summaries_dict:
                    s = _ensure_extractive_summary(summaries_dict[theme], supabase, run_id, theme)
                    color = THEME_COLORS.get(theme, "#666")

                    # Fetch actual articles for this theme
                    articles = supabase.get_articles_for_run(run_id, theme) or []
                    if source_filter_val:
                        articles = [a for a in articles if a.get("source_name") == source_filter_val]

                    article_count = s.get("article_count", 0)
                    if article_count == 0:
                        article_count = len(articles)

                    with st.expander(f"🔹 {theme} ({article_count} articles)"):
                        col_details, col_articles = st.columns([2, 1])

                        with col_details:
                            st.markdown("**WHAT HAPPENED**")
                            st.markdown(sanitize_summary_html(s.get('what_is_happening', 'No data.')))

                            st.markdown("**SIGNIFICANCE**")
                            st.write(s.get('why_it_matters', 'No analysis.'))

                            st.markdown("**WATCHLIST**")
                            st.write(s.get('what_to_watch', 'No items.'))

                        with col_articles:
                            st.markdown("**TRACKED ARTICLES**")
                            if articles:
                                for a in articles:
                                    st.markdown(f"• **{a['title']}**")
                                    st.caption(f"Source: {a['source_name']}")
                                    if a.get("link"):
                                        st.markdown(f"&nbsp;&nbsp;[Read link →]({a['link']})")
                            else:
                                st.write("No matching articles tracked in this run.")
            st.markdown("<br>", unsafe_allow_html=True)

        # Pagination footer
        st.divider()
        col_prev, col_page, col_next = st.columns([1, 2, 1])
        with col_prev:
            if st.session_state.wiki_page_index > 0:
                if st.button("⬅️ Previous", key="btn_prev_page"):
                    st.session_state.wiki_page_index -= 1
                    st.rerun()
        with col_page:
            st.markdown(f"<div style='text-align: center; line-height: 38px;'>Page <b>{st.session_state.wiki_page_index + 1}</b> of <b>{max_pages}</b> ({total_runs} total runs)</div>", unsafe_allow_html=True)
        with col_next:
            if offset + 5 < total_runs:
                if st.button("Next ➡️", key="btn_next_page"):
                    st.session_state.wiki_page_index += 1
                    st.rerun()
    else:
        st.info("No runs found for this page.")


# ---------------------------------------------------------------------------
# Compare tab
# ---------------------------------------------------------------------------

def _render_compare_tab(supabase, selected_theme="All Themes", source_filter_val=None):
    """Render the side-by-side run comparison tool."""
    st.subheader("⚖️ Compare Two Runs Side-by-Side")
    st.caption("Select two different runs below to compare their theme summaries side-by-side.")

    # Load up to 30 recent runs for selection
    all_runs_for_comp = supabase.get_all_runs(limit=30)
    if all_runs_for_comp and len(all_runs_for_comp) >= 2:
        run_options = {f"{format_display_timestamp(r['run_timestamp'])} (Articles: {r['total_articles']})": r for r in all_runs_for_comp}
        options_keys = list(run_options.keys())

        c_run1, c_run2 = st.columns(2)
        with c_run1:
            run1_label = st.selectbox("Select First Run (older/newer):", options_keys, index=1)
        with c_run2:
            run2_label = st.selectbox("Select Second Run (older/newer):", options_keys, index=0)

        run1 = run_options[run1_label]
        run2 = run_options[run2_label]

        # Fetch summaries for both runs
        sum1_list = supabase.get_summaries_for_run(run1["id"]) or []
        sum2_list = supabase.get_summaries_for_run(run2["id"]) or []

        sum1_dict = {s["theme_name"]: s for s in sum1_list}
        sum2_dict = {s["theme_name"]: s for s in sum2_list}

        st.divider()

        themes_to_compare = THEME_ORDER if selected_theme == "All Themes" else [selected_theme]

        for theme in themes_to_compare:
            if theme in sum1_dict or theme in sum2_dict:
                theme_color = THEME_COLORS.get(theme, "#666")

                st.markdown(f"### {theme}")
                col_run_a, col_run_b = st.columns(2)

                with col_run_a:
                    st.subheader(f"📅 Run: {format_display_timestamp(run1['run_timestamp'])}")
                    if theme in sum1_dict:
                        s1 = _ensure_extractive_summary(sum1_dict[theme], supabase, run1['id'], theme)
                        st.markdown("**What is Happening:**")
                        st.markdown(sanitize_summary_html(s1.get("what_is_happening", "")))
                        st.markdown("**Significance:**")
                        st.write(s1.get("why_it_matters", ""))
                        st.markdown("**Watchlist:**")
                        st.write(s1.get("what_to_watch", ""))
                    else:
                        st.info("Theme not found in this run.")

                with col_run_b:
                    st.subheader(f"📅 Run: {format_display_timestamp(run2['run_timestamp'])}")
                    if theme in sum2_dict:
                        s2 = _ensure_extractive_summary(sum2_dict[theme], supabase, run2['id'], theme)
                        st.markdown("**What is Happening:**")
                        st.markdown(sanitize_summary_html(s2.get("what_is_happening", "")))
                        st.markdown("**Significance:**")
                        st.write(s2.get("why_it_matters", ""))
                        st.markdown("**Watchlist:**")
                        st.write(s2.get("what_to_watch", ""))
                    else:
                        st.info("Theme not found in this run.")

                st.divider()
    else:
        st.info("At least 2 runs must exist in the database to perform comparison.")

if __name__ == "__main__":
    main()
