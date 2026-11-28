"""
AI Pulse - Memory Wiki Page
Browse the historical timeline, compare runs, and inspect monthly rollups.
(Sage chat lives on its dedicated page, pages/11_Sage.py — Phase 7.)
"""

import streamlit as st
from datetime import datetime, timedelta
import pandas as pd

from config.themes import THEME_ORDER, THEME_COLORS
from core.history_manager import load_full_history, compute_run_processing_stats
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
    render_sidebar_nav(current="memory")

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
        st.info("ℹ️ Running in Local Mode: showing latest cached run. Connect Supabase to unlock the full timeline search & comparison.")
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
        _rs = compute_run_processing_stats(summaries)
        if _rs["new_unprocessed"] or _rs["processed"]:
            st.caption(
                f"🆕 {_rs['new_unprocessed']} new unprocessed articles this run · "
                f"✅ {_rs['processed']} summarised · ⏳ {_rs['waiting']} waiting for next run"
            )

        themes_to_show = THEME_ORDER if selected_theme == "All Themes" else [selected_theme]
        cols = st.columns(min(len(themes_to_show), 2))

        col_idx = 0
        for theme in themes_to_show:
            if theme in summaries:
                summary = ensure_extractive_summary(summaries[theme], articles=entry.get("themed_articles", {}).get(theme, []))
                color = THEME_COLORS.get(theme, "#666")
                count = counts.get(theme, 0)
                gen_log_loc = summary.get("_generation_log") or summary.get("generation_log") or {}
                if isinstance(gen_log_loc, str):
                    try:
                        import json as _json
                        gen_log_loc = _json.loads(gen_log_loc)
                    except Exception:
                        gen_log_loc = {}
                inc_loc = summary.get("included_count") if summary.get("included_count") is not None else gen_log_loc.get("included_count")
                tot_loc = summary.get("total_articles") or gen_log_loc.get("total_articles") or count
                waiting_loc = summary.get("waiting_hashes")
                if waiting_loc is None:
                    waiting_loc = gen_log_loc.get("waiting_hashes")
                left_loc = len(waiting_loc) if waiting_loc is not None else max(0, tot_loc - (inc_loc or 0))
                if inc_loc is not None and tot_loc > 0:
                    if inc_loc == 0 and left_loc == 0:
                        label_loc = f"📰 {tot_loc} tracked · 0 new (all previously processed)"
                    else:
                        label_loc = f"📰 {inc_loc} of {tot_loc} reflected · {left_loc} left unprocessed"
                else:
                    label_loc = f"📰 {count} articles"
                skipped_loc = summary.get("skipped_articles") or gen_log_loc.get("skipped_articles") or []
                aged_loc = summary.get("aged_out_articles") or gen_log_loc.get("aged_out_articles") or []

                with cols[col_idx % len(cols)]:
                    st.markdown(f"""
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; border-bottom: 1px solid #444; padding-bottom: 5px;">
                        <span class="theme-pill" style="background-color: {color};">{theme}</span>
                        <span style="font-size: 12px; color: #aaa;">{label_loc}</span>
                    </div>
                    """, unsafe_allow_html=True)

                    st.markdown("**WHAT HAPPENED**")
                    st.write(summary.get('what_is_happening', 'No data.'))

                    st.markdown("**SIGNIFICANCE**")
                    st.write(summary.get('why_it_matters', 'No analysis.'))

                    st.markdown("**WATCHLIST**")
                    st.write(summary.get('what_to_watch', 'No items.'))

                    if skipped_loc:
                        with st.expander(f"⏳ Missed / Backlog Articles ({len(skipped_loc)})"):
                            for sk in skipped_loc:
                                st.markdown(f"• **{sk.get('title', 'Unknown')}**")
                                if sk.get("source"):
                                    st.caption(f"Source: {sk['source']}")
                                if sk.get("link"):
                                    st.markdown(f"&nbsp;&nbsp;[Read link →]({sk['link']})")
                    if aged_loc:
                        with st.expander(f"🍂 Aged-out Unprocessed ({len(aged_loc)})"):
                            st.caption("Left the fetch window before ever being summarised.")
                            for ag in aged_loc:
                                st.markdown(f"• **{ag.get('title', 'Unknown')}**")
                                if ag.get("last_seen"):
                                    st.caption(f"Last seen: {ag['last_seen']}")
                                if ag.get("link"):
                                    st.markdown(f"&nbsp;&nbsp;[Read link →]({ag['link']})")
                    st.divider()

                col_idx += 1
        return

    # --- Supabase Powered Mode: 2-tab layout ---
    # (Ask Sage moved to its dedicated page, pages/11_Sage.py — Phase 7.)
    tab_timeline, tab_compare = st.tabs(["📖 Memory Timeline", "⚖️ Compare Runs"])

    # =========================================================================
    # TAB 1: MEMORY TIMELINE
    # =========================================================================
    with tab_timeline:
        _render_timeline_tab(supabase, selected_theme, source_filter_val)

    # =========================================================================
    # TAB 2: COMPARE RUNS
    # =========================================================================
    with tab_compare:
        _render_compare_tab(supabase, selected_theme, source_filter_val)


# ---------------------------------------------------------------------------
# Timeline tab
# ---------------------------------------------------------------------------
# (The Ask Sage tab and its helpers moved to pages/11_Sage.py — Phase 7.)

def _render_monthly_syntheses(supabase, selected_theme):
    """Read-only Tier-3 monthly syntheses expander (Phase 6.1).

    Silent when the table is empty, missing (pre-migration), or Supabase
    is unavailable — the rollups are additive, never a hard dependency.
    """
    rows = supabase.get_monthly_syntheses() or []
    if not rows:
        return
    themes_to_show = THEME_ORDER if selected_theme == "All Themes" else [selected_theme]
    rows = [r for r in rows if r.get("theme_name") in themes_to_show]
    if not rows:
        return

    with st.expander(f"🗓️ Monthly Syntheses (Tier 3) — {len(rows)} rollup(s)", expanded=False):
        st.caption(
            "Macro monthly rollups per theme, synthesized from the daily briefs. "
            "Completed months are final; the current month refreshes every two weeks."
        )
        by_month = {}
        for r in rows:
            by_month.setdefault(r.get("year_month", ""), []).append(r)
        for year_month in sorted(by_month, reverse=True):
            st.markdown(f"#### {year_month}")
            month_rows = {r["theme_name"]: r for r in by_month[year_month]}
            for theme in themes_to_show:
                r = month_rows.get(theme)
                if not r:
                    continue
                color = THEME_COLORS.get(theme, "#666")
                st.markdown(
                    f'<div style="border-left:4px solid {color};padding-left:10px;margin:8px 0 4px 0;">'
                    f'<strong>{theme}</strong></div>',
                    unsafe_allow_html=True,
                )
                st.markdown(sanitize_summary_html(r.get("macro_summary", "")), unsafe_allow_html=True)
                milestones = r.get("key_milestones") or []
                if isinstance(milestones, list) and milestones:
                    with st.expander(f"🔑 Key milestones ({len(milestones)})", expanded=False):
                        for m in milestones:
                            if not isinstance(m, dict):
                                continue
                            date_str = m.get("date") or ""
                            line = f"- **{date_str}** — {m.get('milestone', '')}" if date_str else f"- {m.get('milestone', '')}"
                            link = m.get("source_link")
                            if isinstance(link, str) and link.startswith("http"):
                                line += f" [link →]({link})"
                            st.markdown(line)
                updated = format_display_timestamp(r.get("updated_at", ""))
                st.caption(
                    f"Updated {updated} · covers {r.get('source_run_count', 0)} runs · "
                    f"{r.get('generation_source') or 'unknown engine'}"
                )


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

    # Tier-3 monthly rollups (Phase 6.1) — read-only, silent pre-migration.
    _render_monthly_syntheses(supabase, selected_theme)

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
            _rs = compute_run_processing_stats(summaries_dict)
            if _rs["new_unprocessed"] or _rs["processed"]:
                st.caption(
                    f"🆕 {_rs['new_unprocessed']} new unprocessed articles this run · "
                    f"✅ {_rs['processed']} summarised · ⏳ {_rs['waiting']} waiting for next run"
                )

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

                    gen_log = s.get("_generation_log") or s.get("generation_log") or {}
                    if isinstance(gen_log, str):
                        try:
                            import json as _json
                            gen_log = _json.loads(gen_log)
                        except Exception:
                            gen_log = {}

                    inc_count = s.get("included_count") if s.get("included_count") is not None else gen_log.get("included_count")
                    tot_count = s.get("total_articles") or gen_log.get("total_articles") or article_count or len(articles)
                    skipped_list = s.get("skipped_articles") or gen_log.get("skipped_articles") or []
                    aged_list = s.get("aged_out_articles") or gen_log.get("aged_out_articles") or []

                    waiting_list = s.get("waiting_hashes")
                    if waiting_list is None:
                        waiting_list = gen_log.get("waiting_hashes")
                    left_count = len(waiting_list) if waiting_list is not None else max(0, tot_count - (inc_count or 0))

                    if inc_count is not None and tot_count > 0:
                        if inc_count == 0 and left_count == 0:
                            expander_title = f"🔹 {theme} ({tot_count} tracked · 0 new — all previously processed)"
                        else:
                            expander_title = f"🔹 {theme} ({inc_count} of {tot_count} reflected · {left_count} left unprocessed)"
                    else:
                        expander_title = f"🔹 {theme} ({article_count} articles)"

                    with st.expander(expander_title):
                        col_details, col_articles = st.columns([2, 1])

                        with col_details:
                            if inc_count is not None and tot_count > 0:
                                if inc_count == 0 and left_count == 0:
                                    st.caption(f"ℹ️ **0 new** of {tot_count} tracked articles — all were processed in earlier runs; nothing left unprocessed.")
                                else:
                                    st.caption(f"ℹ️ **{inc_count} of {tot_count}** classified articles reflected in this summary · **{left_count} left unprocessed** for the next run.")
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

                            if skipped_list:
                                st.markdown("---")
                                st.markdown(f"**⏳ MISSED / BACKLOG ARTICLES ({len(skipped_list)})**")
                                st.caption("Classified under this theme but omitted from summary due to budget/ranking limits.")
                                for sk in skipped_list:
                                    st.markdown(f"• **{sk.get('title', 'Unknown')}**")
                                    if sk.get("source"):
                                        st.caption(f"Source: {sk['source']}")
                                    if sk.get("link"):
                                        st.markdown(f"&nbsp;&nbsp;[Read link →]({sk['link']})")

                            if aged_list:
                                st.markdown("---")
                                st.markdown(f"**🍂 AGED-OUT UNPROCESSED ({len(aged_list)})**")
                                st.caption("Waiting articles that left the fetch window before ever being summarised — recorded, not silently lost.")
                                for ag in aged_list:
                                    st.markdown(f"• **{ag.get('title', 'Unknown')}**")
                                    meta = []
                                    if ag.get("source"):
                                        meta.append(f"Source: {ag['source']}")
                                    if ag.get("last_seen"):
                                        meta.append(f"Last seen: {ag['last_seen']}")
                                    if meta:
                                        st.caption(" · ".join(meta))
                                    if ag.get("link"):
                                        st.markdown(f"&nbsp;&nbsp;[Read link →]({ag['link']})")
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
