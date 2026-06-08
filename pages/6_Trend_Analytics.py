"""
AI Pulse - Trend Analytics Page
Visualises thematic momentum, keyword velocity, and engineering vs hype ratios over time.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime

from config.themes import THEME_ORDER, THEME_COLORS
from core.history_manager import load_full_history
from core.bg_refresher import check_and_show_bg_status
from core.shared_sidebar import render_sidebar_nav
from core.supabase_client import get_supabase_manager

# Set page config
st.set_page_config(page_title="Trend Analytics - AI Pulse", page_icon="📈", layout="wide")

# Legacy mapping to ensure older runs map seamlessly to updated theme names
LEGACY_THEME_MAPPING = {
    "AI Applications & Architecture": "Agentic Systems & DevTools",
    "AI Models": "Frontier Models & Benchmarks",
    "AI Infrastructure": "Hardware, Compute & LLMOps",
    "AI Companies & Business": "Enterprise Strategy & ROI",
    "AI in Government & Policy": "Governance, Safety & Policy"
}

def load_analytics_data_fallback(history):
    """Parse history.json into structured pandas DataFrames (local fallback)."""
    data = []
    for ts, entry in history.items():
        date = entry.get("date", ts[:10])
        counts_raw = entry.get("counts", {})
        
        # Map raw counts using the legacy naming system
        counts = {}
        for k, v in counts_raw.items():
            mapped_key = LEGACY_THEME_MAPPING.get(k, k)
            counts[mapped_key] = counts.get(mapped_key, 0) + v
        
        row = {"timestamp": ts, "date": date}
        for theme in THEME_ORDER:
            row[theme] = counts.get(theme, 0)
        data.append(row)
        
    df_themes = pd.DataFrame(data)
    if not df_themes.empty:
        df_themes = df_themes.sort_values("timestamp")
    return df_themes

def load_analytics_data_supabase(supabase):
    """Fetch and parse theme article counts from Supabase."""
    counts_list = supabase.get_theme_article_counts_by_run()
    if not counts_list:
        return pd.DataFrame()
        
    runs_dict = {}
    for item in counts_list:
        ts = item["run_timestamp"]
        dt = item["run_date"]
        theme = item["theme_name"]
        cnt = item["count"]
        
        if ts not in runs_dict:
            runs_dict[ts] = {"timestamp": ts, "date": dt}
            
        mapped_theme = LEGACY_THEME_MAPPING.get(theme, theme)
        runs_dict[ts][mapped_theme] = runs_dict[ts].get(mapped_theme, 0) + cnt
        
    data = []
    for ts, row in runs_dict.items():
        for theme in THEME_ORDER:
            if theme not in row:
                row[theme] = 0
        data.append(row)
        
    df_themes = pd.DataFrame(data)
    if not df_themes.empty:
        df_themes = df_themes.sort_values("timestamp")
    return df_themes

def load_keyword_data(supabase, selected_keywords, history=None):
    """Load keyword counts per run from either Supabase or local history fallback."""
    if supabase.is_available():
        keyword_list = supabase.get_keyword_velocity(selected_keywords)
        if keyword_list:
            kw_runs = {}
            for item in keyword_list:
                ts = item["run_timestamp"]
                dt = item["run_date"]
                kw = item["keyword"]
                cnt = item["count"]
                if ts not in kw_runs:
                    kw_runs[ts] = {"timestamp": ts, "date": dt}
                kw_runs[ts][kw] = cnt
                
            kw_data = []
            for ts, row in kw_runs.items():
                for kw in selected_keywords:
                    if kw not in row:
                        row[kw] = 0
                kw_data.append(row)
            df_keywords = pd.DataFrame(kw_data)
            if not df_keywords.empty:
                df_keywords = df_keywords.sort_values("timestamp")
            return df_keywords

    # Local fallback
    if not history:
        return pd.DataFrame()
        
    keyword_data = []
    for ts, entry in history.items():
        date = entry.get("date", ts[:10])
        full_text = ""
        for article in entry.get("full_articles", []):
            full_text += f" {article.get('title', '')} {article.get('summary', '')}"
        full_text = full_text.lower()
        
        kw_row = {"timestamp": ts, "date": date}
        for kw in selected_keywords:
            kw_row[kw] = full_text.count(kw.lower())
        keyword_data.append(kw_row)
        
    df_keywords = pd.DataFrame(keyword_data)
    if not df_keywords.empty:
        df_keywords = df_keywords.sort_values("timestamp")
    return df_keywords

def main():
    # 1. Top of page alert if background update finished
    check_and_show_bg_status()
    
    st.title("📈 Trend & Momentum Analytics")
    st.markdown("### Longitudinal analysis of AI technical shifts and enterprise signals")
    st.markdown("---")
    
    # Render shared sidebar navigation
    render_sidebar_nav()
    
    # Initialize Supabase manager
    supabase = get_supabase_manager()
    using_supabase = False
    df_themes = pd.DataFrame()
    history = {}
    
    if supabase.is_available():
        with st.spinner("Fetching historical runs from Supabase..."):
            df_themes = load_analytics_data_supabase(supabase)
            if not df_themes.empty:
                using_supabase = True
                
    if not using_supabase:
        # Graceful fallback
        history = load_full_history()
        if history:
            st.info("ℹ️ Running in Local Mode: displaying cached local history. Connect Supabase for the full dataset.")
            df_themes = load_analytics_data_fallback(history)
            
    if df_themes.empty or len(df_themes) < 2:
        st.warning("📊 Analytics requires at least 2 historical runs. Perform a data refresh on the main dashboard to gather historical data.")
        st.page_link("app.py", label="Go to Home Dashboard", icon="🏠")
        return
        
    # Visual grid
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("📊 Thematic Momentum")
        st.caption("Volume of tracked articles per theme across historical intelligence runs")
        
        # Melt DataFrame for plotly line plot
        df_melted_themes = df_themes.melt(id_vars=["date"], value_vars=THEME_ORDER, var_name="Theme", value_name="Articles")
        
        fig_themes = px.line(
            df_melted_themes,
            x="date",
            y="Articles",
            color="Theme",
            color_discrete_map=THEME_COLORS,
            markers=True,
        )
        fig_themes.update_layout(
            template="plotly_dark",
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            xaxis_title="Run Date",
            yaxis_title="Article Volume",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(l=0, r=0, t=30, b=0)
        )
        st.plotly_chart(fig_themes, width="stretch")
        
    with col2:
        st.subheader("🚀 Keyword Velocity Analytics")
        st.caption("Mention frequency of targeted AI keywords in gathered articles")
        
        default_kws = ["mcp", "blackwell", "gpu", "rag", "agents", "regulation", "eu ai act", "benchmark"]
        
        # Keyword Configuration
        custom_kws = st.text_input(
            "💡 Add custom keywords (comma-separated) to search article titles/summaries:",
            value=""
        )
        
        keywords_list = list(default_kws)
        if custom_kws:
            for kw in custom_kws.split(","):
                kw_clean = kw.strip().lower()
                if kw_clean and kw_clean not in keywords_list:
                    keywords_list.append(kw_clean)
                    
        selected_keywords = st.multiselect(
            "Select keywords to visualize:",
            options=keywords_list,
            default=["mcp", "blackwell", "rag", "eu ai act"]
        )
        
        if selected_keywords:
            with st.spinner("Calculating keyword frequencies..."):
                df_keywords = load_keyword_data(supabase, selected_keywords, history)
                
            if not df_keywords.empty and any(kw in df_keywords.columns for kw in selected_keywords):
                # Filter to only the selected columns that actually exist
                available_kw = [kw for kw in selected_keywords if kw in df_keywords.columns]
                df_melted_kw = df_keywords.melt(id_vars=["date"], value_vars=available_kw, var_name="Keyword", value_name="Mentions")
                
                fig_kw = px.line(
                    df_melted_kw,
                    x="date",
                    y="Mentions",
                    color="Keyword",
                    markers=True,
                )
                fig_kw.update_layout(
                    template="plotly_dark",
                    paper_bgcolor='rgba(0,0,0,0)',
                    plot_bgcolor='rgba(0,0,0,0)',
                    xaxis_title="Run Date",
                    yaxis_title="Mention Count",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    margin=dict(l=0, r=0, t=30, b=0)
                )
                st.plotly_chart(fig_kw, width="stretch")
            else:
                st.info("No matching mentions found for the selected keywords.")
        else:
            st.warning("Please select or configure at least one keyword.")
            
    st.markdown("---")
    
    # Heatmap Grid
    st.subheader("🔥 Theme Intensity Heatmap")
    st.caption("At-a-glance visualization of theme activity and trends across all runs")
    
    df_melted_all = df_themes.melt(id_vars=["date"], value_vars=THEME_ORDER, var_name="Theme", value_name="Articles")
    df_pivot = df_melted_all.pivot_table(
        index="Theme",
        columns="date",
        values="Articles",
        aggfunc="sum",
        fill_value=0,
    )

    fig_heatmap = px.imshow(
        df_pivot,
        labels=dict(x="Run Date", y="Theme", color="Articles"),
        x=df_pivot.columns,
        y=df_pivot.index,
        color_continuous_scale="Viridis",
        aspect="auto"
    )
    fig_heatmap.update_layout(
        template="plotly_dark",
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        margin=dict(l=0, r=0, t=10, b=0)
    )
    st.plotly_chart(fig_heatmap, width="stretch")
    
    st.markdown("---")
    
    # 3. Macro engineering-to-hype index
    st.subheader("⚖️ Hype vs. Engineering Signal Index")
    st.caption("Comparing Deep Tech (Agentic Systems, Frontier Models, Hardware) against Market Hype and Regulatory Policy (Strategy, Governance)")
    
    df_themes["Engineering"] = (
        df_themes.get("Agentic Systems & DevTools", 0) + 
        df_themes.get("Frontier Models & Benchmarks", 0) + 
        df_themes.get("Hardware, Compute & LLMOps", 0)
    )
    df_themes["Business & Governance"] = (
        df_themes.get("Enterprise Strategy & ROI", 0) + 
        df_themes.get("Governance, Safety & Policy", 0)
    )
    
    fig_idx = go.Figure()
    fig_idx.add_trace(go.Scatter(
        x=df_themes["date"],
        y=df_themes["Engineering"],
        name="Engineering & Technical Breakdowns",
        line=dict(color="#1f77b4", width=3),
        mode="lines+markers"
    ))
    fig_idx.add_trace(go.Scatter(
        x=df_themes["date"],
        y=df_themes["Business & Governance"],
        name="Enterprise Strategy & Governance Narrative",
        line=dict(color="#d62728", width=3),
        mode="lines+markers"
    ))
    
    # Compute areas for shade
    fig_idx.update_layout(
        template="plotly_dark",
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        xaxis_title="Run Date",
        yaxis_title="Combined Article Count",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=0, r=0, t=10, b=0)
    )
    st.plotly_chart(fig_idx, width="stretch")
    
    # 4. Detailed Theme Drilldown
    st.markdown("---")
    st.subheader("🔍 Detailed Theme Historical Drilldown")
    st.caption("Select a theme to view its historical trajectory, summaries, and evolution over time.")
    
    selected_drilldown_theme = st.selectbox(
        "Select a theme to inspect:",
        options=THEME_ORDER
    )
    
    if selected_drilldown_theme:
        if using_supabase:
            with st.spinner(f"Loading historical summaries for {selected_drilldown_theme}..."):
                history_summaries = supabase.get_theme_history(selected_drilldown_theme, limit=10)
                
            if history_summaries:
                # Plotly sparkline for the selected theme
                df_single_theme = df_themes[["date", selected_drilldown_theme]].copy()
                fig_spark = px.line(
                    df_single_theme,
                    x="date",
                    y=selected_drilldown_theme,
                    title=f"Article Count Trajectory: {selected_drilldown_theme}",
                    markers=True
                )
                fig_spark.update_traces(line_color=THEME_COLORS.get(selected_drilldown_theme, "#1f77b4"))
                fig_spark.update_layout(
                    template="plotly_dark",
                    paper_bgcolor='rgba(0,0,0,0)',
                    plot_bgcolor='rgba(0,0,0,0)',
                    height=200,
                    margin=dict(l=0, r=0, t=40, b=0)
                )
                st.plotly_chart(fig_spark, width="stretch")
                
                # Show chronological summaries
                st.markdown("### Chronological Summaries (Newest First)")
                for summary in history_summaries:
                    # Let's link summary back to a run date
                    run_record = supabase.get_run_by_id(summary["run_id"])
                    run_date = run_record["run_date"] if run_record else "Unknown Date"
                    
                    with st.expander(f"📅 Run on {run_date} ({summary.get('article_count', 0)} articles)"):
                        col_a, col_b, col_c = st.columns(3)
                        with col_a:
                            st.markdown("**What is Happening**")
                            st.write(summary.get("what_is_happening", "N/A"))
                        with col_b:
                            st.markdown("**Why It Matters**")
                            st.write(summary.get("why_it_matters", "N/A"))
                        with col_c:
                            st.markdown("**Watchlist & Next Steps**")
                            st.write(summary.get("what_to_watch", "N/A"))
            else:
                st.info("No historical summaries found in Supabase for this theme.")
        else:
            # Fallback local display
            st.info("ℹ️ Drilldown timeline relies on Supabase. In local mode, we display the current run details.")
            last_run = load_full_history()
            if last_run:
                latest_ts = sorted(last_run.keys(), reverse=True)[0]
                entry = last_run[latest_ts]
                summaries = entry.get("summaries", {})
                theme_summary = summaries.get(selected_drilldown_theme, {})
                
                st.markdown(f"#### Latest Run: {latest_ts}")
                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    st.markdown("**What is Happening**")
                    st.write(theme_summary.get("what_is_happening", "N/A"))
                with col_b:
                    st.markdown("**Why It Matters**")
                    st.write(theme_summary.get("why_it_matters", "N/A"))
                with col_c:
                    st.markdown("**Watchlist & Next Steps**")
                    st.write(theme_summary.get("what_to_watch", "N/A"))

if __name__ == "__main__":
    main()
