"""
AI Pulse - Memory Wiki Page
View past runs, search articles, compare runs, and track the evolution of AI developments over time.
"""

import streamlit as st
from datetime import datetime, timedelta
import pandas as pd

from config.themes import THEME_ORDER, THEME_COLORS
from core.history_manager import load_full_history
from core.shared_sidebar import render_sidebar_nav
from core.supabase_client import get_supabase_manager

# Page configuration
st.set_page_config(
    page_title="Memory Wiki - AI Pulse",
    page_icon="🧠",
    layout="wide"
)

# Custom CSS for the Wiki look
st.markdown("""
<style>
    .wiki-date {
        font-size: 22px;
        font-weight: bold;
        color: #e0e0e0;
        margin-top: 20px;
        margin-bottom: 15px;
        border-bottom: 2px solid #3b3e4a;
        padding-bottom: 5px;
    }
    .theme-pill {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 15px;
        font-size: 12px;
        font-weight: bold;
        margin-right: 5px;
        color: white;
    }
</style>
""", unsafe_allow_html=True)

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
    
    # Search box at the top
    search_query = st.text_input("🔍 Search article titles & summaries across all history:", value="")
    
    if not using_supabase:
        # Fallback to local history
        st.info("ℹ️ Running in Local Mode: showing latest cached run. Connect Supabase to unlock full timeline search & comparison.")
        history = load_full_history()
        
        if not history:
            st.warning("No historical data found. Run a data refresh on the main dashboard to start building your wiki.")
            st.page_link("app.py", label="Back to Dashboard", icon="🏠")
            return
            
        latest_ts = sorted(history.keys(), reverse=True)[0]
        entry = history[latest_ts]
        summaries = entry.get("summaries", {})
        counts = entry.get("counts", {})
        
        st.markdown(f'<div class="wiki-date">📅 Latest Cache: {latest_ts}</div>', unsafe_allow_html=True)
        
        themes_to_show = THEME_ORDER if selected_theme == "All Themes" else [selected_theme]
        cols = st.columns(min(len(themes_to_show), 2))
        
        col_idx = 0
        for theme in themes_to_show:
            if theme in summaries:
                summary = summaries[theme]
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
        
    # --- Supabase Powered Mode ---
    
    if search_query:
        # Display Search Mode Results
        st.subheader(f"Search Results for '{search_query}'")
        with st.spinner("Searching records..."):
            articles = supabase.search_articles(
                query=search_query,
                theme_filter=theme_filter_val,
                date_from=date_from_val,
                date_to=date_to_val,
                source_filter=source_filter_val,
                limit=50
            )
            
        if articles:
            # Group articles by theme for structured display
            for theme in THEME_ORDER:
                theme_arts = [a for a in articles if a["theme_name"] == theme]
                if theme_arts:
                    theme_color = THEME_COLORS.get(theme, "#666")
                    with st.expander(f"📁 {theme} ({len(theme_arts)} matches)", expanded=True):
                        for a in theme_arts:
                            st.markdown(f"📰 **{a['title']}**")
                            st.caption(f"Source: {a['source_name']} | Published: {a.get('published_at', 'N/A')[:10]}")
                            if a.get("summary"):
                                st.write(a["summary"])
                            if a.get("link"):
                                st.markdown(f"[Read full article →]({a['link']})")
                            st.divider()
        else:
            st.info("No matching articles found.")
        return

    # Dual-tab view: Timeline Browse & Comparison
    tab_timeline, tab_compare = st.tabs(["📖 Memory Timeline", "⚖️ Compare Runs"])
    
    with tab_timeline:
        # Load paginated runs from Supabase
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
                total_arts = run["total_articles"]
                
                st.markdown(
                    f'<div class="wiki-date">📅 Run: {run_ts} <span style="font-size:14px;font-weight:normal;color:#aaa;">({total_arts} total articles)</span></div>',
                    unsafe_allow_html=True
                )
                
                summaries = supabase.get_summaries_for_run(run_id) or []
                summaries_dict = {s["theme_name"]: s for s in summaries}
                
                themes_to_show = THEME_ORDER if selected_theme == "All Themes" else [selected_theme]
                
                for theme in themes_to_show:
                    if theme in summaries_dict:
                        s = summaries_dict[theme]
                        color = THEME_COLORS.get(theme, "#666")
                        article_count = s.get("article_count", 0)
                        
                        with st.expander(f"🔹 {theme} ({article_count} articles)"):
                            col_details, col_articles = st.columns([2, 1])
                            
                            with col_details:
                                st.markdown("**WHAT HAPPENED**")
                                st.write(s.get('what_is_happening', 'No data.'))
                                
                                st.markdown("**SIGNIFICANCE**")
                                st.write(s.get('why_it_matters', 'No analysis.'))
                                
                                st.markdown("**WATCHLIST**")
                                st.write(s.get('what_to_watch', 'No items.'))
                                
                            with col_articles:
                                st.markdown("**TRACKED ARTICLES**")
                                articles = supabase.get_articles_for_run(run_id, theme) or []
                                
                                # Apply filters on client-side if loaded from runs
                                if source_filter_val:
                                    articles = [a for a in articles if a["source_name"] == source_filter_val]
                                    
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

    with tab_compare:
        st.subheader("⚖️ Compare Two Runs Side-by-Side")
        st.caption("Select two different runs below to compare their theme summaries side-by-side.")
        
        # Load up to 30 recent runs for selection
        all_runs_for_comp = supabase.get_all_runs(limit=30)
        if all_runs_for_comp and len(all_runs_for_comp) >= 2:
            run_options = {f"{r['run_timestamp']} (Articles: {r['total_articles']})": r for r in all_runs_for_comp}
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
            
            for theme in THEME_ORDER:
                if theme in sum1_dict or theme in sum2_dict:
                    theme_color = THEME_COLORS.get(theme, "#666")
                    
                    st.markdown(f"### {theme}")
                    col_run_a, col_run_b = st.columns(2)
                    
                    with col_run_a:
                        st.subheader(f"📅 Run: {run1['run_timestamp']}")
                        if theme in sum1_dict:
                            s1 = sum1_dict[theme]
                            st.markdown("**What is Happening:**")
                            st.write(s1.get("what_is_happening", ""))
                            st.markdown("**Significance:**")
                            st.write(s1.get("why_it_matters", ""))
                            st.markdown("**Watchlist:**")
                            st.write(s1.get("what_to_watch", ""))
                        else:
                            st.info("Theme not found in this run.")
                            
                    with col_run_b:
                        st.subheader(f"📅 Run: {run2['run_timestamp']}")
                        if theme in sum2_dict:
                            s2 = sum2_dict[theme]
                            st.markdown("**What is Happening:**")
                            st.write(s2.get("what_is_happening", ""))
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
