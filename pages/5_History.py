"""
AI Pulse - Memory Wiki Page
Chat with Sage, browse the historical timeline, compare runs, and track the evolution of AI developments.
"""

import streamlit as st
from datetime import datetime, timedelta
import pandas as pd

from config.themes import THEME_ORDER, THEME_COLORS
from core.history_manager import load_full_history
from core.summariser import ensure_extractive_summary
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
                summary = ensure_extractive_summary(summaries[theme], articles=entry.get("themed_articles", {}).get(theme, []))
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

def _render_sage_tab(supabase, default_theme_filter=None, default_date_from=None, default_date_to=None, default_source_filter=None):
    """Render the Ask Sage conversational chat interface with in-depth scope control and persistent history."""
    from datetime import date, datetime, timedelta
    from core.sage_agent import SAGE_INTRO, build_wiki_context, chat_with_sage
    from core.llm_client import LLMClient
    from core.sage_conversations import (
        list_saved_conversations,
        get_conversation,
        save_conversation,
        delete_conversation,
        export_conversation_markdown,
    )
    from config.themes import THEME_ORDER

    # Sage intro banner
    st.markdown(f"""
    <div class="sage-intro">
        <h3>🔮 Sage — AI Research Analyst</h3>
        <p>"{SAGE_INTRO}"</p>
    </div>
    """, unsafe_allow_html=True)

    # Initialise session state keys
    if "sage_messages" not in st.session_state:
        st.session_state.sage_messages = []
    if "sage_current_conv_id" not in st.session_state:
        st.session_state.sage_current_conv_id = None
    if "sage_conv_title" not in st.session_state:
        st.session_state.sage_conv_title = ""
    if "sage_period_preset" not in st.session_state:
        st.session_state.sage_period_preset = "📅 Last 30 Days"
    if "sage_selected_theme" not in st.session_state:
        st.session_state.sage_selected_theme = "All Themes" if not default_theme_filter else default_theme_filter

    today = date.today()

    # -------------------------------------------------------------------------
    # 1. Conversation Management Bar
    # -------------------------------------------------------------------------
    saved_convs = list_saved_conversations()

    conv_options = {"__active__": f"💬 Current Session ({len(st.session_state.sage_messages)} msgs)"}
    for c in saved_convs:
        title_snippet = c["title"][:38] + ("…" if len(c["title"]) > 38 else "")
        time_snippet = c["updated_at"][:10] if c.get("updated_at") else ""
        conv_options[c["id"]] = f"📁 {title_snippet} ({c['message_count']} msgs · {time_snippet})"

    active_id = st.session_state.sage_current_conv_id or "__active__"
    if active_id not in conv_options:
        active_id = "__active__"

    col_conv_select, col_new, col_save, col_export, col_del = st.columns([4, 1.3, 1.4, 1.4, 1.1])

    with col_conv_select:
        selected_conv_key = st.selectbox(
            "Conversation History",
            options=list(conv_options.keys()),
            format_func=lambda k: conv_options.get(k, k),
            index=list(conv_options.keys()).index(active_id),
            label_visibility="collapsed",
            key="sage_thread_selector",
        )
        if selected_conv_key != active_id and selected_conv_key != "__active__":
            loaded = get_conversation(selected_conv_key)
            if loaded:
                st.session_state.sage_messages = loaded.get("messages", [])
                st.session_state.sage_current_conv_id = loaded.get("id")
                st.session_state.sage_conv_title = loaded.get("title", "")
                if loaded.get("theme_filter"):
                    st.session_state.sage_selected_theme = loaded["theme_filter"]
                if loaded.get("period_label"):
                    st.session_state.sage_period_preset = loaded["period_label"]
                st.rerun()

    with col_new:
        if st.button("➕ New", key="sage_btn_new", help="Start a new blank conversation"):
            st.session_state.sage_messages = []
            st.session_state.sage_current_conv_id = None
            st.session_state.sage_conv_title = ""
            st.rerun()

    with col_save:
        can_save = len(st.session_state.sage_messages) > 0
        save_label = "💾 Update" if st.session_state.sage_current_conv_id else "💾 Save"
        if st.button(save_label, key="sage_btn_save", disabled=not can_save, help="Save conversation to persistent history"):
            saved_id = save_conversation(
                messages=st.session_state.sage_messages,
                conv_id=st.session_state.sage_current_conv_id,
                title=st.session_state.sage_conv_title or None,
                theme_filter=st.session_state.sage_selected_theme,
                period_label=st.session_state.sage_period_preset,
            )
            st.session_state.sage_current_conv_id = saved_id
            st.toast("✅ Conversation saved!", icon="💾")
            st.rerun()

    with col_export:
        can_export = len(st.session_state.sage_messages) > 0
        if can_export:
            export_title = st.session_state.sage_conv_title or "Sage_Analysis"
            md_text = export_conversation_markdown(
                title=export_title,
                messages=st.session_state.sage_messages,
                theme_filter=st.session_state.sage_selected_theme,
                period_label=st.session_state.sage_period_preset,
            )
            st.download_button(
                label="📥 Export",
                data=md_text,
                file_name=f"sage_report_{datetime.now().strftime('%Y%m%d_%H%M')}.md",
                mime="text/markdown",
                help="Download conversation as Markdown report",
                key="sage_btn_export",
            )
        else:
            st.button("📥 Export", disabled=True, key="sage_btn_export_disabled")

    with col_del:
        is_saved = bool(st.session_state.sage_current_conv_id)
        if st.button("🗑️", key="sage_btn_delete", disabled=not is_saved, help="Delete active saved conversation"):
            delete_conversation(st.session_state.sage_current_conv_id)
            st.session_state.sage_messages = []
            st.session_state.sage_current_conv_id = None
            st.session_state.sage_conv_title = ""
            st.toast("Conversation deleted.", icon="🗑️")
            st.rerun()

    # -------------------------------------------------------------------------
    # 2. Scope & Research Filters (Period, Theme, Source)
    # -------------------------------------------------------------------------
    with st.expander("🔭 **Research Scope & Historical Filters**", expanded=False):
        c_period, c_theme, c_src = st.columns(3)

        with c_period:
            period_options = [
                "📅 Last 30 Days",
                "⚡ Last 7 Days",
                f"🍂 Current Month ({today.strftime('%B %Y')})",
                "♾️ Full Archive (All Available Dates)",
                "🗓️ Custom Date Range",
            ]
            current_preset_idx = period_options.index(st.session_state.sage_period_preset) if st.session_state.sage_period_preset in period_options else 0
            selected_preset = st.selectbox(
                "Time Period",
                period_options,
                index=current_preset_idx,
                key="sage_period_select_input",
            )
            st.session_state.sage_period_preset = selected_preset

        with c_theme:
            all_themes_opt = ["All Themes"] + THEME_ORDER
            theme_choice = st.selectbox(
                "Focus Theme",
                all_themes_opt,
                index=all_themes_opt.index(st.session_state.sage_selected_theme) if st.session_state.sage_selected_theme in all_themes_opt else 0,
                key="sage_theme_select_input",
            )
            st.session_state.sage_selected_theme = theme_choice

        with c_src:
            sources = ["All Sources"]
            if supabase and supabase.is_available():
                unique_sources = supabase.get_unique_sources()
                if unique_sources:
                    sources.extend(unique_sources)
            selected_source = st.selectbox("Source Filter", sources, key="sage_source_select_input")

        # Resolve date boundaries based on preset
        custom_c1, custom_c2 = st.columns(2)
        if selected_preset == "⚡ Last 7 Days":
            resolved_date_from = (today - timedelta(days=7)).strftime("%Y-%m-%d")
            resolved_date_to = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        elif selected_preset == "📅 Last 30 Days":
            resolved_date_from = (today - timedelta(days=30)).strftime("%Y-%m-%d")
            resolved_date_to = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        elif selected_preset.startswith("🍂 Current Month"):
            resolved_date_from = today.replace(day=1).strftime("%Y-%m-%d")
            resolved_date_to = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        elif selected_preset == "♾️ Full Archive (All Available Dates)":
            resolved_date_from = None
            resolved_date_to = None
        else:  # Custom Range
            with custom_c1:
                start_d = st.date_input("From Date", value=today - timedelta(days=60), key="sage_custom_start")
            with custom_c2:
                end_d = st.date_input("To Date", value=today, key="sage_custom_end")
            resolved_date_from = start_d.strftime("%Y-%m-%d")
            resolved_date_to = (end_d + timedelta(days=1)).strftime("%Y-%m-%d")

    # If expander was not rendered/evaluated, fall back to resolved dates
    if "resolved_date_from" not in locals():
        if st.session_state.sage_period_preset == "⚡ Last 7 Days":
            resolved_date_from = (today - timedelta(days=7)).strftime("%Y-%m-%d")
            resolved_date_to = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        elif st.session_state.sage_period_preset == "📅 Last 30 Days":
            resolved_date_from = (today - timedelta(days=30)).strftime("%Y-%m-%d")
            resolved_date_to = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        elif st.session_state.sage_period_preset.startswith("🍂 Current Month"):
            resolved_date_from = today.replace(day=1).strftime("%Y-%m-%d")
            resolved_date_to = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        elif st.session_state.sage_period_preset == "♾️ Full Archive (All Available Dates)":
            resolved_date_from = None
            resolved_date_to = None
        else:
            resolved_date_from = (today - timedelta(days=60)).strftime("%Y-%m-%d")
            resolved_date_to = (today + timedelta(days=1)).strftime("%Y-%m-%d")

    active_theme_filter = None if st.session_state.sage_selected_theme == "All Themes" else st.session_state.sage_selected_theme
    active_source_filter = None if (not 'selected_source' in locals() or selected_source == "All Sources") else selected_source

    # Render Active Scope Badge
    theme_badge = active_theme_filter or "All Themes"
    date_display = f"{resolved_date_from} → {resolved_date_to}" if resolved_date_from else "Full Archive (Chronological)"
    st.markdown(
        f'<div style="font-size:13px; color:#9aa0a6; padding: 6px 12px; border-radius: 6px; background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08); margin-bottom: 12px;">'
        f'🔭 <b>Active Scope:</b> <code>{st.session_state.sage_period_preset}</code> ({date_display}) &nbsp;|&nbsp; '
        f'<b>Theme:</b> <code>{theme_badge}</code> &nbsp;|&nbsp; '
        f'<b>Source:</b> <code>{active_source_filter or "All"}</code>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if LLMClient.is_quota_exceeded():
        st.info("⚡ **Live Gemini Fallback Active**: Primary Ollama quota is paused. Sage conversations are powered by Google Gemini.", icon="⚡")

    # If empty conversation, show starter prompts
    if not st.session_state.sage_messages:
        st.markdown(
            """
            <div style="background: rgba(255,255,255,0.02); border: 1px dashed #3c4043; border-radius: 8px; padding: 14px 18px; margin: 12px 0 16px 0;">
                <span style="font-size: 13px; color: #8ab4f8; font-weight: 600;">💡 SUGGESTED QUESTIONS:</span>
                <ul style="font-size: 13px; color: #bdc1c6; margin: 8px 0 0 16px; padding: 0;">
                    <li><i>"What were the most important developments across September 2026?"</i></li>
                    <li><i>"How did reasoning models and latent reasoning evolve recently?"</i></li>
                    <li><i>"When did we first see autonomous coding agent benchmarks like Terminal-Bench?"</i></li>
                    <li><i>"What are the major engineering tradeoffs reported in recent model deployments?"</i></li>
                </ul>
            </div>
            """,
            unsafe_allow_html=True,
        )

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
            with st.spinner("Sage is analyzing the archive..."):
                wiki_ctx = build_wiki_context(
                    supabase=supabase,
                    question=user_input,
                    theme_filter=active_theme_filter,
                    date_from=resolved_date_from,
                    date_to=resolved_date_to,
                    source_filter=active_source_filter,
                )

                llm = LLMClient()
                sage_response = chat_with_sage(
                    llm_client=llm,
                    messages=st.session_state.sage_messages,
                    wiki_context=wiki_ctx,
                )

            st.markdown(sage_response)

            stats = wiki_ctx if isinstance(wiki_ctx, dict) else {}
            if stats.get("date_count"):
                st.caption(
                    f"📚 Grounded in **{stats['run_count']}** summaries across "
                    f"**{stats['date_count']}** dates ({stats['date_range']})"
                )
            elif stats.get("run_count") == 0:
                st.caption("ℹ️ *Notice: No wiki records matched the selected period/theme filters.*")

        st.session_state.sage_messages.append({"role": "assistant", "content": sage_response})

        # Auto-update saved conversation thread if one is active
        if st.session_state.sage_current_conv_id:
            save_conversation(
                messages=st.session_state.sage_messages,
                conv_id=st.session_state.sage_current_conv_id,
                title=st.session_state.sage_conv_title or None,
                theme_filter=st.session_state.sage_selected_theme,
                period_label=st.session_state.sage_period_preset,
            )

        st.rerun()





# ---------------------------------------------------------------------------
# Timeline tab
# ---------------------------------------------------------------------------

def _render_timeline_tab(supabase, selected_theme, source_filter_val):
    """Render the existing Memory Timeline browser."""
    total_runs = supabase.get_total_run_count()

    if total_runs == 0:
        st.warning("No runs found in Supabase. Run a data refresh on the main dashboard to populate the database.")
    # Auto-purge specified low-article runs if present
    TARGET_PURGE_TIMES = ["23:34:29", "23:10:42", "23:10:21", "22:35:44", "22:25:28", "22:20:47"]
    all_recent_runs = supabase.get_all_runs(limit=50) or []
    for r in all_recent_runs:
        ts_str = str(r.get("run_timestamp", ""))
        if any(t in ts_str for t in TARGET_PURGE_TIMES):
            supabase.delete_run(r["id"])

    # Re-query total runs after auto-purge
    total_runs = supabase.get_total_run_count()
    if total_runs == 0:
        st.info("No runs found in Memory Wiki timeline.")
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

            col_hdr, col_btn = st.columns([5, 1])
            with col_hdr:
                st.markdown(
                    f'<div class="wiki-date">📅 Run: {format_display_timestamp(run_ts)} <span style="font-size:14px;font-weight:normal;color:#aaa;">({total_arts} total articles tracked)</span></div>',
                    unsafe_allow_html=True
                )
            with col_btn:
                if st.button("🗑️ Purge Run", key=f"purge_{run_id}"):
                    if supabase.delete_run(run_id):
                        st.success(f"Purged run {format_display_timestamp(run_ts)}")
                        st.rerun()

            summaries = supabase.get_summaries_for_run(run_id) or []
            summaries_dict = {s["theme_name"]: s for s in summaries}

            themes_to_show = THEME_ORDER if selected_theme == "All Themes" else [selected_theme]

            for theme in themes_to_show:
                if theme in summaries_dict:
                    s = ensure_extractive_summary(summaries_dict[theme], supabase, run_id, theme)
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
                        s1 = ensure_extractive_summary(sum1_dict[theme], supabase, run1['id'], theme)
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
                        s2 = ensure_extractive_summary(sum2_dict[theme], supabase, run2['id'], theme)
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
