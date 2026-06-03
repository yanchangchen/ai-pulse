"""
Emerging Trends Visualization Page
Tracks and visualizes emerging trends with novelty scoring, acceleration detection, and timeline.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import Supabase client
from core.supabase_client import get_supabase_manager

st.set_page_config(page_title="Emerging Trends", page_icon="🚀", layout="wide")

def get_trend_runs_data() -> pd.DataFrame:
    """Fetch all trend runs from Supabase."""
    try:
        supabase = get_supabase_manager()
        if not supabase.is_available():
            return pd.DataFrame()
        
        response = supabase.client.table("trend_runs").select(
            "id, run_timestamp, run_date, total_articles"
        ).order("run_timestamp", desc=False).execute()
        
        if response.data:
            df = pd.DataFrame(response.data)
            df["run_timestamp"] = pd.to_datetime(df["run_timestamp"])
            df["run_date"] = pd.to_datetime(df["run_date"])
            return df
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"Failed to fetch trend runs: {e}")
        return pd.DataFrame()


def get_theme_articles_by_date(theme_name: str) -> pd.DataFrame:
    """Get articles for a theme grouped by run date."""
    try:
        supabase = get_supabase_manager()
        if not supabase.is_available():
            return pd.DataFrame()
        
        response = supabase.client.table("articles").select(
            "id, theme_name, run_id, published_at, title, source_name"
        ).eq("theme_name", theme_name).execute()
        
        if response.data:
            df = pd.DataFrame(response.data)
            df["published_at"] = pd.to_datetime(df["published_at"])
            return df
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"Failed to fetch articles for {theme_name}: {e}")
        return pd.DataFrame()


def calculate_emergence_metrics(theme_name: str, runs_df: pd.DataFrame) -> Dict:
    """
    Calculate emergence metrics for a theme:
    - First appearance date
    - Acceleration (week-over-week growth %)
    - Novelty score (0-100)
    """
    try:
        articles_df = get_theme_articles_by_date(theme_name)
        
        if articles_df.empty:
            return {
                "first_seen": None,
                "days_old": None,
                "acceleration": 0,
                "novelty_score": 0,
                "current_count": 0,
                "trend": "stable"
            }
        
        # Get first appearance
        first_article = articles_df["published_at"].min()
        days_old = (datetime.now() - first_article).days if first_article else None
        
        # Count articles by week
        articles_df["week"] = articles_df["published_at"].dt.isocalendar().week
        articles_df["year"] = articles_df["published_at"].dt.isocalendar().year
        
        weekly_counts = articles_df.groupby(["year", "week"]).size()
        
        # Calculate acceleration (week-over-week growth)
        acceleration = 0
        trend = "stable"
        if len(weekly_counts) >= 2:
            prev_week = weekly_counts.iloc[-2]
            curr_week = weekly_counts.iloc[-1]
            if prev_week > 0:
                acceleration = ((curr_week - prev_week) / prev_week) * 100
                if acceleration > 20:
                    trend = "accelerating"
                elif acceleration < -20:
                    trend = "declining"
        
        # Novelty score: based on how recent the trend is
        # Recent trends (< 7 days) get higher scores
        if days_old is not None:
            if days_old <= 7:
                novelty_score = 100
            elif days_old <= 14:
                novelty_score = 80
            elif days_old <= 30:
                novelty_score = 60
            else:
                novelty_score = max(0, 100 - (days_old - 30) * 2)
        else:
            novelty_score = 0
        
        return {
            "first_seen": first_article,
            "days_old": days_old,
            "acceleration": acceleration,
            "novelty_score": int(novelty_score),
            "current_count": len(articles_df),
            "trend": trend
        }
    except Exception as e:
        logger.error(f"Failed to calculate emergence metrics for {theme_name}: {e}")
        return {
            "first_seen": None,
            "days_old": None,
            "acceleration": 0,
            "novelty_score": 0,
            "current_count": 0,
            "trend": "stable"
        }


def render_emergence_timeline():
    """Render timeline of theme emergence."""
    st.subheader("🗓️ Theme Emergence Timeline")
    
    runs_df = get_trend_runs_data()
    if runs_df.empty:
        st.info("No trend data available yet.")
        return
    
    # Get all themes
    from config.themes import THEME_ORDER
    
    timeline_data = []
    for theme in THEME_ORDER:
        metrics = calculate_emergence_metrics(theme, runs_df)
        if metrics["first_seen"]:
            timeline_data.append({
                "Theme": theme,
                "First Seen": metrics["first_seen"],
                "Days Old": metrics["days_old"],
                "Novelty Score": metrics["novelty_score"],
                "Trend": metrics["trend"],
                "Article Count": metrics["current_count"]
            })
    
    if timeline_data:
        timeline_df = pd.DataFrame(timeline_data).sort_values("First Seen", ascending=False)
        
        # Create timeline visualization
        fig = px.scatter(
            timeline_df,
            x="First Seen",
            y="Theme",
            size="Article Count",
            color="Novelty Score",
            hover_data=["Days Old", "Trend", "Article Count"],
            color_continuous_scale="YlOrRd",
            title="Theme Emergence Timeline (Bubble size = Article count)"
        )
        
        fig.update_layout(height=400, hovermode="closest")
        st.plotly_chart(fig, use_container_width=True)
        
        # Show table
        st.dataframe(timeline_df, use_container_width=True)
    else:
        st.info("No emergence data available yet.")


def render_acceleration_index():
    """Render acceleration index for themes."""
    st.subheader("📈 Acceleration Index (Week-over-Week Growth %)")
    
    from config.themes import THEME_ORDER
    
    runs_df = get_trend_runs_data()
    if runs_df.empty:
        st.info("No trend data available yet.")
        return
    
    acceleration_data = []
    for theme in THEME_ORDER:
        metrics = calculate_emergence_metrics(theme, runs_df)
        acceleration_data.append({
            "Theme": theme,
            "Acceleration %": metrics["acceleration"],
            "Trend": metrics["trend"],
            "Current Articles": metrics["current_count"]
        })
    
    accel_df = pd.DataFrame(acceleration_data).sort_values("Acceleration %", ascending=True)
    
    # Create bar chart
    colors = ["#d62728" if x < -20 else "#ff7f0e" if x > 20 else "#1f77b4" 
              for x in accel_df["Acceleration %"]]
    
    fig = go.Figure(data=[
        go.Bar(
            y=accel_df["Theme"],
            x=accel_df["Acceleration %"],
            orientation="h",
            marker=dict(color=colors),
            text=accel_df["Acceleration %"].round(1),
            textposition="outside",
            hovertemplate="<b>%{y}</b><br>Growth: %{x:.1f}%<extra></extra>"
        )
    ])
    
    fig.update_layout(
        title="Week-over-Week Growth Rate by Theme",
        xaxis_title="Growth %",
        yaxis_title="Theme",
        height=400,
        showlegend=False
    )
    
    st.plotly_chart(fig, use_container_width=True)
    
    # Show table
    st.dataframe(accel_df, use_container_width=True)


def render_novelty_scoring():
    """Render novelty scoring visualization."""
    st.subheader("⭐ Novelty Score (How New is This Trend?)")
    
    from config.themes import THEME_ORDER
    
    runs_df = get_trend_runs_data()
    if runs_df.empty:
        st.info("No trend data available yet.")
        return
    
    novelty_data = []
    for theme in THEME_ORDER:
        metrics = calculate_emergence_metrics(theme, runs_df)
        novelty_data.append({
            "Theme": theme,
            "Novelty Score": metrics["novelty_score"],
            "Days Old": metrics["days_old"],
            "First Seen": metrics["first_seen"]
        })
    
    novelty_df = pd.DataFrame(novelty_data).sort_values("Novelty Score", ascending=False)
    
    # Create gauge chart
    col1, col2 = st.columns(2)
    
    with col1:
        # Top emerging themes
        st.write("**Top Emerging Themes**")
        top_themes = novelty_df.head(5)
        for idx, row in top_themes.iterrows():
            score = row["Novelty Score"]
            days = row["Days Old"]
            emoji = "🔥" if score >= 80 else "📈" if score >= 60 else "📊"
            st.write(f"{emoji} **{row['Theme']}**: {score}/100 (Seen {days} days ago)")
    
    with col2:
        # Novelty distribution
        fig = px.histogram(
            novelty_df,
            x="Novelty Score",
            nbins=10,
            title="Distribution of Novelty Scores",
            labels={"Novelty Score": "Novelty Score", "count": "Number of Themes"}
        )
        st.plotly_chart(fig, use_container_width=True)


def render_new_articles_detection():
    """Render detection of new/novel articles in the last 7 days."""
    st.subheader("🆕 Novel Articles (Last 7 Days)")
    
    try:
        supabase = get_supabase_manager()
        if not supabase.is_available():
            st.info("Supabase not available.")
            return
        
        # Get articles from last 7 days
        seven_days_ago = (datetime.now() - timedelta(days=7)).isoformat()
        
        response = supabase.client.table("articles").select(
            "id, theme_name, title, source_name, published_at, link"
        ).gte("published_at", seven_days_ago).order("published_at", desc=True).execute()
        
        if response.data:
            articles_df = pd.DataFrame(response.data)
            articles_df["published_at"] = pd.to_datetime(articles_df["published_at"])
            
            # Group by theme
            for theme in articles_df["theme_name"].unique():
                theme_articles = articles_df[articles_df["theme_name"] == theme]
                
                with st.expander(f"**{theme}** ({len(theme_articles)} new articles)"):
                    for idx, row in theme_articles.iterrows():
                        pub_date = row["published_at"].strftime("%Y-%m-%d")
                        st.write(f"📰 **{row['title']}**")
                        st.write(f"   Source: {row['source_name']} | Published: {pub_date}")
                        if row.get("link"):
                            st.write(f"   [Read more →]({row['link']})")
                        st.divider()
        else:
            st.info("No new articles in the last 7 days.")
    except Exception as e:
        logger.error(f"Failed to fetch novel articles: {e}")
        st.error(f"Error fetching novel articles: {e}")


def main():
    from core.bg_refresher import check_and_show_bg_status, render_sidebar_info

    # 1. Top of page alert if background update finished
    check_and_show_bg_status()

    # Sidebar Navigation
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

    st.title("🚀 Emerging Trends Analysis")
    
    st.markdown("""
    Track and analyze emerging trends in AI & Engineering.
    
    **Metrics:**
    - **Emergence Timeline**: When did each trend first appear?
    - **Acceleration Index**: Which trends are growing fastest?
    - **Novelty Score**: How new/recent is each trend? (0-100)
    - **Novel Articles**: New articles published in the last 7 days
    """)
    
    st.divider()
    
    # Render visualizations
    render_emergence_timeline()
    st.divider()
    
    render_acceleration_index()
    st.divider()
    
    render_novelty_scoring()
    st.divider()
    
    render_new_articles_detection()


if __name__ == "__main__":
    main()
