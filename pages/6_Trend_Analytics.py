"""
AI Pulse - Trend Analytics Page
Provides cross-run longitudinal trend analytics, theme momentum charts, and timeline drilldowns.
"""

import streamlit as st
import pandas as pd
import plotly.express as px

from config.themes import THEME_ORDER, THEME_COLORS
from core.history_manager import load_full_history
from core.supabase_client import get_supabase_manager
from core.design_system import apply_design_system, get_plotly_theme_layout
from core.shared_sidebar import render_sidebar_nav
from core.bg_refresher import check_and_show_bg_status

# Page configuration
st.set_page_config(
    page_title="Trend Analytics - AI Pulse",
    page_icon="📈",
    layout="wide"
)

# Apply central design system
apply_design_system()


def main() -> None:
    """Main trend analytics page."""
    check_and_show_bg_status()
    render_sidebar_nav()

    st.title("📈 Trend Analytics")
    st.markdown("### Longitudinal Thematic Momentum & Historical Trajectories")
    st.divider()

    supabase = get_supabase_manager()
    using_supabase = supabase.is_available()

    if using_supabase:
        with st.spinner("Loading longitudinal metrics from Supabase..."):
            runs_summary = supabase.get_runs_summary(limit=10)

        if not runs_summary:
            st.info("No historical runs found in database. Run the intelligence pipeline to accumulate data.")
            return

        df_runs = pd.DataFrame(runs_summary)
        df_runs = df_runs.sort_values("run_timestamp")

        matrix_data = []
        for run in df_runs.to_dict("records"):
            summaries = supabase.get_summaries_by_run(run["id"])
            row = {"date": run["run_date"], "timestamp": run["run_timestamp"], "total": run["total_articles"]}
            for theme in THEME_ORDER:
                row[theme] = summaries.get(theme, {}).get("article_count", 0)
            matrix_data.append(row)

        df_themes = pd.DataFrame(matrix_data)

    else:
        history = load_full_history()
        if not history:
            st.info("No historical data found locally. Run the intelligence pipeline to accumulate data.")
            return

        matrix_data = []
        for ts, entry in sorted(history.items()):
            row = {"date": entry.get("date", ts[:10]), "timestamp": ts, "total": len(entry.get("full_articles", []))}
            themed = entry.get("themed_articles", {})
            for theme in THEME_ORDER:
                row[theme] = len(themed.get(theme, []))
            matrix_data.append(row)

        df_themes = pd.DataFrame(matrix_data)

    # 1. Cross-Run Theme Momentum Chart
    st.subheader("📊 Cross-Run Theme Momentum (Article Volume over Time)")

    df_melted_themes = df_themes.melt(
        id_vars=["date"],
        value_vars=THEME_ORDER,
        var_name="Theme",
        value_name="Articles"
    )

    fig_themes = px.line(
        df_melted_themes,
        x="date",
        y="Articles",
        color="Theme",
        color_discrete_map=THEME_COLORS,
        markers=True,
    )
    theme_layout = get_plotly_theme_layout()
    theme_layout.update({
        "xaxis_title": "Run Date",
        "yaxis_title": "Article Volume",
        "legend": dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        "margin": dict(l=0, r=0, t=30, b=0)
    })
    fig_themes.update_layout(**theme_layout)
    st.plotly_chart(fig_themes, width="stretch")

    # 2. Detailed Theme Historical Drilldown
    st.divider()
    st.subheader("🔍 Detailed Theme Historical Drilldown")
    st.caption("Select a theme to view its historical trajectory and summaries over time.")

    selected_drilldown_theme = st.selectbox(
        "Select a theme to inspect:",
        options=THEME_ORDER
    )

    if selected_drilldown_theme:
        if using_supabase:
            with st.spinner(f"Loading historical summaries for {selected_drilldown_theme}..."):
                history_summaries = supabase.get_theme_history(selected_drilldown_theme, limit=10)

            if history_summaries:
                df_single_theme = df_themes[["date", selected_drilldown_theme]].copy()
                fig_spark = px.line(
                    df_single_theme,
                    x="date",
                    y=selected_drilldown_theme,
                    title=f"Article Count Trajectory: {selected_drilldown_theme}",
                    markers=True
                )
                fig_spark.update_traces(line_color=THEME_COLORS.get(selected_drilldown_theme, "#1f77b4"))
                spark_layout = get_plotly_theme_layout()
                spark_layout.update({
                    "height": 220,
                    "margin": dict(l=0, r=0, t=40, b=0)
                })
                fig_spark.update_layout(**spark_layout)
                st.plotly_chart(fig_spark, width="stretch")

                st.markdown("### Chronological Summaries (Newest First)")
                for summary in history_summaries:
                    run_record = supabase.get_run_by_id(summary["run_id"])
                    run_date = run_record.get("run_date", "Unknown Date") if run_record else "Unknown Date"
                    with st.expander(f"📅 Run Date: {run_date} ({summary.get('article_count', 0)} articles)"):
                        st.markdown(f"**The Signal:** {summary.get('what_is_happening', '')}")
                        st.markdown(f"**Significance:** {summary.get('why_it_matters', '')}")
                        st.markdown(f"**Watchlist:** {summary.get('what_to_watch', '')}")
            else:
                st.info(f"No historical summaries found for {selected_drilldown_theme}.")
        else:
            st.info("Supabase is offline. Detailed historical drilldowns require Supabase cloud persistence.")


if __name__ == "__main__":
    main()
