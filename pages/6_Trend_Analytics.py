"""
AI Pulse - Trend Analytics Page
Longitudinal trend intelligence: normalised theme momentum, baseline-vs-
emerging keyword signals, monthly macro arcs (Tier 3), and per-theme
historical drilldowns.
"""

import streamlit as st
import pandas as pd
import plotly.express as px

from config.themes import THEMES, THEME_ORDER, THEME_COLORS
from core.history_manager import load_full_history
from core.supabase_client import get_supabase_manager
from core.design_system import apply_design_system, get_plotly_theme_layout, sanitize_summary_html, format_display_timestamp
from core.shared_sidebar import render_sidebar_nav
from core.bg_refresher import check_and_show_bg_status
from core.trend_stats import (
    classify_keywords,
    moving_average,
    specialist_keywords,
    theme_share_matrix,
)

# Page configuration
st.set_page_config(
    page_title="Trend Analytics - AI Pulse",
    page_icon="📈",
    layout="wide"
)

apply_design_system()

RUNS_HORIZON = 30          # ~a month of runs, not a week
RECENT_WINDOW = 3          # "now" for emerging detection
BASELINE_WINDOW = 10       # trailing baseline


def _load_theme_matrix(supabase, using_supabase):
    """Per-run theme article counts + totals, chronological ascending."""
    if using_supabase:
        with st.spinner("Loading longitudinal metrics from Supabase..."):
            runs_summary = supabase.get_runs_summary(limit=RUNS_HORIZON)
        if not runs_summary:
            return None, None
        df_runs = pd.DataFrame(runs_summary).sort_values("run_timestamp")
        matrix_data = []
        for run in df_runs.to_dict("records"):
            summaries = supabase.get_summaries_by_run(run["id"])
            row = {"date": run["run_date"], "timestamp": run["run_timestamp"],
                   "total": run["total_articles"]}
            for theme in THEME_ORDER:
                row[theme] = summaries.get(theme, {}).get("article_count", 0)
            matrix_data.append(row)
        return pd.DataFrame(matrix_data), df_runs
    history = load_full_history()
    if not history:
        return None, None
    matrix_data = []
    for ts, entry in sorted(history.items()):
        row = {"date": entry.get("date", ts[:10]), "timestamp": ts,
               "total": len(entry.get("full_articles", []))}
        themed = entry.get("themed_articles", {})
        for theme in THEME_ORDER:
            row[theme] = len(themed.get(theme, []))
        matrix_data.append(row)
    return pd.DataFrame(matrix_data), None


def _render_momentum(df_themes):
    st.subheader("📊 Theme Momentum (normalised)")
    st.caption(
        "Share of each run's articles per theme — normalising away source-volume "
        "noise — with a 3-run moving average. Raw counts are in the expander."
    )
    share = theme_share_matrix(df_themes, THEME_ORDER)
    smoothed = moving_average(share, THEME_ORDER, window=3)
    melted = smoothed.melt(id_vars=["date"], value_vars=THEME_ORDER,
                           var_name="Theme", value_name="Share")
    fig = px.line(melted, x="date", y="Share", color="Theme",
                  color_discrete_map=THEME_COLORS, markers=True)
    layout = get_plotly_theme_layout()
    layout.update({
        "xaxis_title": "Run Date", "yaxis_title": "Share of run articles (%)",
        "legend": dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        "margin": dict(l=0, r=0, t=30, b=0),
    })
    fig.update_layout(**layout)
    st.plotly_chart(fig, width="stretch")

    with st.expander("Raw per-run values (share % and article counts)", expanded=False):
        tab_share, tab_counts = st.tabs(["Share %", "Article counts"])
        with tab_share:
            st.dataframe(share.set_index("date").round(1), width="stretch")
        with tab_counts:
            st.dataframe(df_themes.set_index("date")[THEME_ORDER], width="stretch")


def _render_baseline_emerging(supabase, using_supabase):
    st.divider()
    st.subheader("🚀 Baseline vs Emerging Topics")
    st.caption(
        f"Specialist keywords (weight ≥3) tracked across runs: **emerging** = the "
        f"last {RECENT_WINDOW} runs average ≥1.5× their prior {BASELINE_WINDOW}-run "
        f"baseline; **baseline** = stable background topics; **fading** = losing "
        f"attention (<½ their baseline)."
    )
    if not using_supabase:
        st.info("Keyword velocity requires Supabase cloud persistence.")
        return
    kws = specialist_keywords(THEMES)
    with st.spinner("Computing keyword velocity..."):
        velocity = supabase.get_keyword_velocity(kws, limit=RECENT_WINDOW + BASELINE_WINDOW)
    signals = classify_keywords(velocity or [], recent_n=RECENT_WINDOW, baseline_n=BASELINE_WINDOW)
    if not signals["n_recent_runs"]:
        st.info("Not enough runs accumulated yet for velocity signals.")
        return

    c_e, c_b, c_f = st.columns(3)
    with c_e:
        st.markdown("**🚀 Emerging** (accelerating)")
        if signals["emerging"]:
            for kw, recent, base, ratio in signals["emerging"][:8]:
                st.markdown(f"- **{kw}** — {recent}/run vs {base} baseline (**{ratio}×**)")
        else:
            st.caption("Nothing accelerating right now.")
    with c_b:
        st.markdown("**🧱 Baseline** (stable background)")
        if signals["baseline"]:
            for kw, recent, base in signals["baseline"][:8]:
                st.markdown(f"- {kw} — {base}/run")
        else:
            st.caption("No stable high-frequency terms yet.")
    with c_f:
        st.markdown("**📉 Fading** (losing attention)")
        if signals["fading"]:
            for kw, recent, base in signals["fading"][:5]:
                st.markdown(f"- {kw} — {recent}/run, was {base}")
        else:
            st.caption("Nothing fading significantly.")


def _render_monthly_arcs(supabase, using_supabase):
    st.divider()
    st.subheader("🗓️ Monthly Macro Arcs (Tier 3)")
    st.caption(
        "The longitudinal story: pre-computed monthly syntheses per theme — the "
        "month's arc plus dated milestones. Completed months are final; the "
        "current month refreshes biweekly."
    )
    if not using_supabase:
        st.info("Monthly arcs require Supabase cloud persistence.")
        return
    rows = supabase.get_monthly_syntheses() or []
    if not rows:
        st.info("No monthly syntheses yet — they generate once a month completes.")
        return
    theme = st.selectbox("Theme:", THEME_ORDER, key="trend_arc_theme")
    theme_rows = sorted(
        [r for r in rows if r.get("theme_name") == theme],
        key=lambda r: r.get("year_month", ""), reverse=True,
    )
    if not theme_rows:
        st.info(f"No monthly arcs for {theme} yet.")
        return
    for r in theme_rows:
        with st.expander(f"🗓️ {r.get('year_month', '')} — covers {r.get('source_run_count', 0)} runs", expanded=False):
            st.markdown(sanitize_summary_html(r.get("macro_summary", "")), unsafe_allow_html=True)
            milestones = r.get("key_milestones") or []
            if isinstance(milestones, list) and milestones:
                st.markdown("**Key milestones:**")
                for m in milestones:
                    if not isinstance(m, dict):
                        continue
                    date_str = m.get("date") or ""
                    line = f"- **{date_str}** — {m.get('milestone', '')}" if date_str else f"- {m.get('milestone', '')}"
                    link = m.get("source_link")
                    if isinstance(link, str) and link.startswith("http"):
                        line += f" [link →]({link})"
                    st.markdown(line)


def _render_drilldown(supabase, using_supabase, df_themes, df_runs):
    st.divider()
    st.subheader("🔍 Detailed Theme Historical Drilldown")
    st.caption("Select a theme to view its historical trajectory and summaries over time.")
    selected = st.selectbox("Select a theme to inspect:", options=THEME_ORDER)
    if not selected or not using_supabase:
        if not using_supabase:
            st.info("Supabase is offline. Detailed historical drilldowns require Supabase cloud persistence.")
        return

    with st.spinner(f"Loading historical summaries for {selected}..."):
        history_summaries = supabase.get_theme_history(selected, limit=10)
    if not history_summaries:
        st.info(f"No historical summaries found for {selected}.")
        return

    df_single = df_themes[["date", selected]].copy()
    fig_spark = px.line(df_single, x="date", y=selected,
                        title=f"Article Count Trajectory: {selected}", markers=True)
    fig_spark.update_traces(line_color=THEME_COLORS.get(selected, "#1f77b4"))
    spark_layout = get_plotly_theme_layout()
    spark_layout.update({"height": 220, "margin": dict(l=0, r=0, t=40, b=0)})
    fig_spark.update_layout(**spark_layout)
    st.plotly_chart(fig_spark, width="stretch")

    # Batch run lookup (was an N+1 get_run_by_id per summary).
    run_lookup = {}
    if df_runs is not None:
        run_lookup = {r["id"]: r for r in df_runs.to_dict("records") if r.get("id")}

    st.markdown("### Chronological Summaries (Newest First)")
    from core.summariser import ensure_extractive_summary
    for summary in history_summaries:
        run_record = run_lookup.get(summary.get("run_id"))
        if run_record is None:
            run_record = supabase.get_run_by_id(summary["run_id"]) or {}
        raw_date = run_record.get("run_timestamp", run_record.get("run_date", "Unknown Date"))
        disp_date = format_display_timestamp(raw_date)
        s = ensure_extractive_summary(summary, supabase, summary.get("run_id", ""), selected)
        with st.expander(f"📅 Run Date: {disp_date} ({s.get('article_count', 0)} articles)"):
            st.markdown(f"**The Signal:** {sanitize_summary_html(s.get('what_is_happening', ''))}")
            st.markdown(f"**Significance:** {s.get('why_it_matters', '')}")
            st.markdown(f"**Watchlist:** {s.get('what_to_watch', '')}")


def main() -> None:
    """Main trend analytics page."""
    check_and_show_bg_status()
    render_sidebar_nav(current="trends")

    st.title("📈 Trend Analytics")
    st.markdown("### Baseline, Emerging Trends & Longitudinal Arcs")
    st.divider()

    supabase = get_supabase_manager()
    using_supabase = supabase.is_available()

    df_themes, df_runs = _load_theme_matrix(supabase, using_supabase)
    if df_themes is None:
        st.info("No historical runs found. Run the intelligence pipeline to accumulate data.")
        return

    _render_momentum(df_themes)
    _render_baseline_emerging(supabase, using_supabase)
    _render_monthly_arcs(supabase, using_supabase)
    _render_drilldown(supabase, using_supabase, df_themes, df_runs)


if __name__ == "__main__":
    main()