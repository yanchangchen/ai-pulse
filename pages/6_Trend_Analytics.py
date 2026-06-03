"""
AI Pulse - Trend Analytics Page
Visualises thematic momentum, keyword velocity, and engineering vs hype ratios over time.
"""

import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime

from config.themes import THEME_ORDER, THEME_COLORS
from core.history_manager import load_full_history
from core.bg_refresher import check_and_show_bg_status, render_sidebar_info

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

def load_analytics_data(history):
    """Parse history.json into structured pandas DataFrames with legacy mapping support."""
    data = []
    keyword_data = []
    
    # Selected high-signal terms to track velocity
    keywords_to_track = ["mcp", "blackwell", "gpu", "rag", "agents", "regulation", "eu ai act", "benchmark"]
    
    for ts, entry in history.items():
        date = entry.get("date", ts[:10])
        counts_raw = entry.get("counts", {})
        
        # Map raw counts using the legacy naming system
        counts = {}
        for k, v in counts_raw.items():
            mapped_key = LEGACY_THEME_MAPPING.get(k, k)
            counts[mapped_key] = counts.get(mapped_key, 0) + v
        
        # 1. Parse Thematic Counts
        row = {"timestamp": ts, "date": date}
        for theme in THEME_ORDER:
            row[theme] = counts.get(theme, 0)
        data.append(row)
        
        # 2. Parse Keyword Frequencies in this run's articles
        full_text = ""
        for article in entry.get("full_articles", []):
            full_text += f" {article.get('title', '')} {article.get('summary', '')}"
        full_text = full_text.lower()
        
        kw_row = {"timestamp": ts, "date": date}
        for kw in keywords_to_track:
            kw_row[kw] = full_text.count(kw)
        keyword_data.append(kw_row)
        
    df_themes = pd.DataFrame(data).sort_values("timestamp")
    df_keywords = pd.DataFrame(keyword_data).sort_values("timestamp")
    return df_themes, df_keywords

def main():
    # 1. Top of page alert if background update finished
    check_and_show_bg_status()
    
    st.title("📈 Trend & Momentum Analytics")
    st.markdown("### Longitudinal analysis of AI technical shifts and enterprise signals")
    st.markdown("---")
    
    history = load_full_history()
    if not history or len(history) < 2:
        st.warning("📊 Analytics requires at least 2 historical runs. Perform a data refresh on the main dashboard to gather historical data.")
        st.page_link("app.py", label="Go to Home Dashboard", icon="🏠")
        return
        
    df_themes, df_keywords = load_analytics_data(history)
    
    # Sidebar control
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
    
    # Visual grid
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("📊 Thematic Momentum")
        st.caption("Volume of tracked articles per theme across historical intelligence runs")
        
        # Dark theme check or manual HSL colors
        fig, ax = plt.subplots(figsize=(10, 6))
        fig.patch.set_facecolor('#0e1117')
        ax.set_facecolor('#1e2130')
        
        for theme in THEME_ORDER:
            color = THEME_COLORS.get(theme, "#666")
            ax.plot(df_themes["date"], df_themes[theme], label=theme.replace("AI ", ""), marker='o', linewidth=2.5, color=color)
            
        ax.set_xlabel("Run Date", color='#888')
        ax.set_ylabel("Article Volume", color='#888')
        ax.tick_params(colors='#888')
        ax.grid(True, linestyle=':', alpha=0.3, color='#444')
        ax.legend(facecolor='#1e2130', edgecolor='none', labelcolor='#ccc')
        plt.xticks(rotation=45)
        st.pyplot(fig)
        
    with col2:
        st.subheader("🚀 Keyword Velocity Analytics")
        st.caption("Mention frequency of targeted 2026 AI standards in gathered summaries")
        
        selected_keywords = st.multiselect(
            "Select keywords to compare:",
            ["mcp", "blackwell", "gpu", "rag", "agents", "regulation", "eu ai act", "benchmark"],
            default=["mcp", "blackwell", "rag", "eu ai act"]
        )
        
        fig_kw, ax_kw = plt.subplots(figsize=(10, 6))
        fig_kw.patch.set_facecolor('#0e1117')
        ax_kw.set_facecolor('#1e2130')
        
        for kw in selected_keywords:
            ax_kw.plot(df_keywords["date"], df_keywords[kw], label=kw.upper(), marker='s', linestyle='--', linewidth=2)
            
        ax_kw.set_xlabel("Run Date", color='#888')
        ax_kw.set_ylabel("Mention Frequency", color='#888')
        ax_kw.tick_params(colors='#888')
        ax_kw.grid(True, linestyle=':', alpha=0.3, color='#444')
        ax_kw.legend(facecolor='#1e2130', edgecolor='none', labelcolor='#ccc')
        plt.xticks(rotation=45)
        st.pyplot(fig_kw)
        
    st.markdown("---")
    
    # 3. Macro engineering-to-hype index
    st.subheader("⚖️ Hype vs. Engineering Signal Index")
    st.caption("Comparing Deep Tech (Agentic Systems, Frontier Models, Hardware) against Market Hype and Regulatory Policy (Strategy, Governance)")
    
    df_themes["Engineering"] = df_themes["Agentic Systems & DevTools"] + df_themes["Frontier Models & Benchmarks"] + df_themes["Hardware, Compute & LLMOps"]
    df_themes["Business & Governance"] = df_themes["Enterprise Strategy & ROI"] + df_themes["Governance, Safety & Policy"]
    
    fig_idx, ax_idx = plt.subplots(figsize=(15, 5))
    fig_idx.patch.set_facecolor('#0e1117')
    ax_idx.set_facecolor('#1e2130')
    
    ax_idx.plot(df_themes["date"], df_themes["Engineering"], label="Engineering & Technical Breakdowns", color="#1f77b4", marker='o', linewidth=3)
    ax_idx.plot(df_themes["date"], df_themes["Business & Governance"], label="Enterprise Strategy & Governance Narrative", color="#d62728", marker='o', linewidth=3)
    
    # Fill signal areas
    ax_idx.fill_between(df_themes["date"], df_themes["Engineering"], df_themes["Business & Governance"], 
                        where=(df_themes["Engineering"] >= df_themes["Business & Governance"]), 
                        interpolate=True, color='green', alpha=0.15, label="High Signal Period")
    ax_idx.fill_between(df_themes["date"], df_themes["Engineering"], df_themes["Business & Governance"], 
                        where=(df_themes["Engineering"] < df_themes["Business & Governance"]), 
                        interpolate=True, color='red', alpha=0.15, label="High Hype Period")
    
    ax_idx.set_xlabel("Run Date", color='#888')
    ax_idx.set_ylabel("Combined Article Count", color='#888')
    ax_idx.tick_params(colors='#888')
    ax_idx.grid(True, linestyle=':', alpha=0.3, color='#444')
    ax_idx.legend(facecolor='#1e2130', edgecolor='none', labelcolor='#ccc')
    plt.xticks(rotation=45)
    st.pyplot(fig_idx)

if __name__ == "__main__":
    main()
