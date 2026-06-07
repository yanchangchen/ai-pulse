"""
AI Pulse - Emerging Trends Visualization Page
Tracks and visualizes emerging trends with composite novelty scoring, acceleration detection, and timeline.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import logging
import re
from collections import Counter

from config.themes import THEME_ORDER, THEME_COLORS
from core.supabase_client import get_supabase_manager
from core.history_manager import load_full_history
from core.shared_sidebar import render_sidebar_nav

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

st.set_page_config(page_title="Emerging Trends - AI Pulse", page_icon="🚀", layout="wide")

# Stop words for keyword extraction
STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "when", "at", "by", "for", "with",
    "about", "against", "between", "into", "through", "during", "before", "after", "above", "below",
    "to", "from", "up", "down", "in", "out", "on", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "all", "any", "both", "each", "few", "more", "most", "other", "some",
    "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very", "s", "t", "can",
    "will", "just", "don", "should", "now", "of", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "having", "do", "does", "did", "doing", "this", "that", "these", "those",
    "i", "you", "he", "she", "it", "we", "they", "my", "your", "his", "her", "its", "our", "their",
    "ai", "artificial", "intelligence", "new", "using", "use", "used", "technology", "development",
    "developments", "system", "systems", "model", "models", "data", "analysis", "report", "reports"
}

def extract_top_keywords(articles_df, limit=5):
    """Extract top frequent terms from article titles and summaries."""
    words = []
    for _, row in articles_df.iterrows():
        text = f"{row['title']} {row.get('summary', '')}".lower()
        found = re.findall(r"\b[a-zA-Z]{3,}\b", text)
        for w in found:
            if w not in STOP_WORDS:
                words.append(w)
    counter = Counter(words)
    return [item[0] for item in counter.most_common(limit)]

def load_all_articles_data(supabase, start_date=None, end_date=None) -> pd.DataFrame:
    """Load all articles in a single batch query with fallback to local history."""
    if supabase.is_available():
        try:
            response = supabase.client.table("articles").select(
                "id, theme_name, run_id, published_at, title, source_name, summary, link"
            ).execute()
            if response.data:
                df = pd.DataFrame(response.data)
                df["published_at"] = pd.to_datetime(df["published_at"])
                if start_date:
                    df = df[df["published_at"].dt.date >= start_date]
                if end_date:
                    df = df[df["published_at"].dt.date <= end_date]
                return df
        except Exception as e:
            logger.error(f"Failed to batch load articles from Supabase: {e}")
            
    # Fallback to local history
    history = load_full_history()
    all_articles = []
    for ts, entry in history.items():
        for article in entry.get("full_articles", []):
            # Try to get theme name from themed_articles mapping
            theme_name = "Other"
            for t_name, t_arts in entry.get("themed_articles", {}).items():
                if any(a.get("content_hash") == article.get("content_hash") or a.get("title") == article.get("title") for a in t_arts):
                    theme_name = t_name
                    break
            
            pub_at = article.get("published_at") or ts
            all_articles.append({
                "id": article.get("id", ""),
                "theme_name": theme_name,
                "published_at": pub_at,
                "title": article.get("title", ""),
                "source_name": article.get("source_name", "Unknown"),
                "summary": article.get("summary", ""),
                "link": article.get("link", "")
            })
            
    if all_articles:
        df = pd.DataFrame(all_articles)
        df["published_at"] = pd.to_datetime(df["published_at"])
        if start_date:
            df = df[df["published_at"].dt.date >= start_date]
        if end_date:
            df = df[df["published_at"].dt.date <= end_date]
        return df
        
    return pd.DataFrame()


def hex_to_rgba(hex_color: str, alpha: float = 0.13) -> str:
    """Convert #RRGGBB to rgba(r,g,b,alpha)."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c*2 for c in hex_color)
    r, g, b = (int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"

def render_sparkline(counts_series, theme_color):
    """Render a clean sparkline using Plotly."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=list(range(len(counts_series))),
        y=list(counts_series),
        mode="lines",
        line=dict(color=theme_color, width=2.5),
        fill="tozeroy",
        hex_to_rgba(theme_color, alpha=0.13)
        #fillcolor=f"{theme_color}22"
    ))
    fig.update_layout(
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        margin=dict(l=0, r=0, t=0, b=0),
        height=35,
        width=120,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False
    )
    return fig

def main():
    # Render shared sidebar navigation
    render_sidebar_nav()
    
    # Sidebar filters
    st.sidebar.subheader("📅 Scope Analysis")
    start_date = st.sidebar.date_input("Start Date", value=datetime.now().date() - timedelta(days=90))
    end_date = st.sidebar.date_input("End Date", value=datetime.now().date())
    
    st.title("🚀 Emerging Trends Analysis")
    st.markdown("""
    Advanced tracking of thematic momentum and newly emerging patterns in the AI landscape.
    *Calculations are performed in real-time on all historical records.*
    """)
    st.divider()
    
    supabase = get_supabase_manager()
    
    with st.spinner("Batch loading intelligence data..."):
        df_articles = load_all_articles_data(supabase, start_date, end_date)
        
    if df_articles.empty:
        st.warning("⚠️ No article data found within the selected dates. Please check your date filters or run a data refresh.")
        return
        
    # Calculate Emergence Metrics in Python
    theme_metrics = []
    theme_scores_radar = {}
    
    for theme in THEME_ORDER:
        theme_arts = df_articles[df_articles["theme_name"] == theme]
        
        if theme_arts.empty:
            continue
            
        # 1. Recency / First appearance
        first_seen = theme_arts["published_at"].min()
        days_old = (datetime.now() - first_seen).days
        
        # Recency score (0-100)
        recency_score = 100 if days_old <= 7 else 85 if days_old <= 14 else 60 if days_old <= 30 else max(0, 100 - (days_old - 30) * 1.5)
        
        # 2. Acceleration: WoW Growth rate
        theme_arts_sorted = theme_arts.sort_values("published_at")
        theme_arts_sorted["week"] = theme_arts_sorted["published_at"].dt.isocalendar().week
        theme_arts_sorted["year"] = theme_arts_sorted["published_at"].dt.isocalendar().year
        weekly_counts = theme_arts_sorted.groupby(["year", "week"]).size()
        
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
                    
        # Acceleration Score (0-100)
        accel_score = min(100, max(0, 50 + acceleration * 0.5))
        
        # 3. Source Diversity
        unique_sources = theme_arts["source_name"].nunique()
        source_div_score = min(100, unique_sources * 10)
        
        # 4. Volume Score
        vol_score = min(100, len(theme_arts) * 3)
        
        # Composite Novelty / Emergence Score (0-100)
        composite_score = int(recency_score * 0.4 + accel_score * 0.3 + source_div_score * 0.3)
        
        # Extract weekly counts for sparkline
        # Fill missing weeks in timeline to ensure line is correct
        all_weeks = pd.date_range(start=df_articles["published_at"].min(), end=df_articles["published_at"].max(), freq='W')
        full_week_counts = []
        for wk in all_weeks:
            wk_num = wk.isocalendar().week
            yr_num = wk.isocalendar().year
            full_week_counts.append(weekly_counts.get((yr_num, wk_num), 0))
            
        top_kws = extract_top_keywords(theme_arts, limit=5)
        
        theme_metrics.append({
            "Theme": theme,
            "First Seen": first_seen,
            "Days Old": days_old,
            "Total Articles": len(theme_arts),
            "Growth WoW %": round(acceleration, 1),
            "Unique Sources": unique_sources,
            "Composite Score": composite_score,
            "Trend": trend,
            "Weekly Counts": full_week_counts[-10:] if len(full_week_counts) >= 10 else full_week_counts,
            "Keywords": top_kws
        })
        
        theme_scores_radar[theme] = {
            "Volume": vol_score,
            "Acceleration": accel_score,
            "Source Diversity": source_div_score,
            "Recency": recency_score
        }
        
    df_metrics = pd.DataFrame(theme_metrics).sort_values("Composite Score", ascending=False)
    
    # Render layout
    col1, col2 = st.columns([3, 2])
    
    with col1:
        st.subheader("📊 Composite Emergence Table")
        st.caption("Overall rank based on Recency, Acceleration, and Source Diversity")
        
        # Format table for display
        df_display = df_metrics.copy()
        df_display["First Seen"] = df_display["First Seen"].dt.strftime("%Y-%m-%d")
        df_display["Trend"] = df_display["Trend"].apply(
            lambda x: "🔥 Accelerating" if x == "accelerating" else "📉 Declining" if x == "declining" else "➡️ Stable"
        )
        st.dataframe(
            df_display[["Theme", "First Seen", "Total Articles", "Growth WoW %", "Unique Sources", "Trend", "Composite Score"]],
            use_container_width=True,
            hide_index=True
        )
        
    with col2:
        st.subheader("🕸️ Momentum Profile Radar")
        st.caption("Theme strengths across Volume, Acceleration, Source Diversity, and Recency")
        
        fig_radar = go.Figure()
        for theme_name, score_dict in theme_scores_radar.items():
            fig_radar.add_trace(go.Scatterpolar(
                r=[
                    score_dict["Volume"],
                    score_dict["Acceleration"],
                    score_dict["Source Diversity"],
                    score_dict["Recency"]
                ],
                theta=["Volume", "Acceleration", "Source Diversity", "Recency"],
                fill="toself",
                name=theme_name,
                line=dict(color=THEME_COLORS.get(theme_name, "#1f77b4"))
            ))
            
        fig_radar.update_layout(
            polar=dict(
                radialaxis=dict(
                    visible=True,
                    range=[0, 100]
                )
            ),
            showlegend=True,
            template="plotly_dark",
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=40, r=40, t=10, b=10)
        )
        st.plotly_chart(fig_radar, use_container_width=True)
        
    st.divider()
    
    # Emerging Trend Cards
    st.subheader("💡 Per-Theme Trend Cards")
    st.caption("Detailed overview of emerging trends, keyword frequency, and weekly article count trajectory.")
    
    # Render cards in 2-column grid
    card_cols = st.columns(2)
    for idx, row in df_metrics.iterrows():
        col_to_use = card_cols[idx % 2]
        
        with col_to_use:
            theme_color = THEME_COLORS.get(row["Theme"], "#1f77b4")
            
            # Custom container styling using standard st components
            with st.container(border=True):
                c_title, c_badge = st.columns([3, 1])
                with c_title:
                    st.markdown(f"### {row['Theme']}")
                with c_badge:
                    badge_emoji = "🔥" if row["Composite Score"] >= 75 else "📈" if row["Composite Score"] >= 50 else "➡️"
                    st.markdown(f"**Score: {row['Composite Score']}** {badge_emoji}")
                    
                col_left, col_right = st.columns([2, 1])
                with col_left:
                    st.write(f"📅 **First Seen**: {row['First Seen'].strftime('%Y-%m-%d')} ({row['Days Old']} days ago)")
                    st.write(f"📰 **Total Articles**: {row['Total Articles']} across **{row['Unique Sources']}** sources")
                    
                    # Keywords display
                    kw_tags = " ".join([f"`{kw}`" for kw in row["Keywords"]])
                    st.write(f"🏷️ **Top Keywords**: {kw_tags}")
                
                with col_right:
                    # Sparkline rendering
                    if row["Weekly Counts"]:
                        st.caption("Weekly Volume")
                        spark_fig = render_sparkline(row["Weekly Counts"], theme_color)
                        st.plotly_chart(spark_fig, use_container_width=True, config={'displayModeBar': False})
                        
                st.divider()
                
                # Show latest article from this theme
                theme_arts = df_articles[df_articles["theme_name"] == row["Theme"]].sort_values("published_at", ascending=False)
                if not theme_arts.empty:
                    latest_art = theme_arts.iloc[0]
                    st.markdown(f"**Latest Highlight**: *{latest_art['title']}*")
                    if latest_art.get("link"):
                        st.markdown(f"[Read article →]({latest_art['link']})")
                        
    st.divider()
    
    # Timeline
    st.subheader("🗓️ Timeline of Theme Emergence")
    fig_timeline = px.scatter(
        df_metrics,
        x="First Seen",
        y="Theme",
        size="Total Articles",
        color="Composite Score",
        hover_data=["Days Old", "Trend", "Total Articles"],
        color_continuous_scale="Viridis",
        title="Chronological Emergence (Bubble size represents volume)"
    )
    fig_timeline.update_layout(
        template="plotly_dark",
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        height=400,
        margin=dict(l=0, r=0, t=30, b=0)
    )
    st.plotly_chart(fig_timeline, use_container_width=True)

if __name__ == "__main__":
    main()
