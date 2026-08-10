"""
AI Pulse - Sources Page
Shows all sources and articles grouped by source, along with source health error tracking
and a live deep diagnostic tool to analyze why specific sources fail.
"""

import streamlit as st
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

from config.sources import SOURCES
from config.themes import THEME_COLORS
from core.fetcher import diagnose_source
from core.design_system import apply_design_system
from core.shared_sidebar import render_sidebar_nav
from core.bg_refresher import check_and_show_bg_status

# Page configuration
st.set_page_config(
    page_title="Sources - AI Pulse",
    page_icon="⚡",
    layout="wide"
)

# Apply central design system
apply_design_system()


def get_session_data():
    """Get articles data from session state."""
    if 'articles' not in st.session_state:
        st.switch_page("app.py")
        st.stop()

    return st.session_state.articles


def main() -> None:
    """Main sources page."""
    articles = get_session_data()

    # Top of page alert if background update finished
    check_and_show_bg_status()

    # Sidebar Navigation
    render_sidebar_nav()

    # Header
    st.title("📰 Sources & Feed Diagnostics Explorer")
    st.markdown("### Registered Engineering Blogs, RSS Feeds & Health Analyzer")
    st.divider()

    # Calculate statistics across all registered sources in config/sources.py
    article_counts_by_source = {}
    for article in articles:
        src = article.get('source_name', 'Unknown')
        article_counts_by_source[src] = article_counts_by_source.get(src, 0) + 1

    registered_sources_data = []
    zero_coverage_sources = []

    for s in SOURCES:
        s_name = s["name"]
        count = article_counts_by_source.get(s_name, 0)
        status = "🟢 Active" if count > 0 else "⚠️ 0 Articles"

        row = {
            "Name": s_name,
            "Status": status,
            "Type": s.get("type", "rss").upper(),
            "Category": s.get("category", "blog").title(),
            "Articles": count,
            "Link": s.get("url", "")
        }
        registered_sources_data.append(row)
        if count == 0:
            zero_coverage_sources.append(s)

    tab_overview, tab_articles, tab_diagnostics = st.tabs([
        "📊 Source Coverage",
        "📋 Articles by Source",
        "🔬 Deep Diagnostics & Error Analysis"
    ])

    # ----------------------------------------------------
    # TAB 1: SOURCE COVERAGE OVERVIEW
    # ----------------------------------------------------
    with tab_overview:
        m1, m2, m3 = st.columns(3)
        m1.metric("Registered Sources", len(SOURCES))
        m2.metric("Active Sources with Articles", len(SOURCES) - len(zero_coverage_sources))
        m3.metric("Sources with 0 Coverage", len(zero_coverage_sources))

        st.markdown("#### Registered Sources Overview")
        if registered_sources_data:
            df_sources = pd.DataFrame(registered_sources_data)
            st.dataframe(
                df_sources,
                column_config={
                    "Name": st.column_config.TextColumn("Source Name", width="medium"),
                    "Status": st.column_config.TextColumn("Status", width="small"),
                    "Type": st.column_config.TextColumn("Type", width="small"),
                    "Category": st.column_config.TextColumn("Category", width="small"),
                    "Articles": st.column_config.ProgressColumn(
                        "Articles Tracked",
                        format="%d",
                        min_value=0,
                        max_value=max((r["Articles"] for r in registered_sources_data), default=1)
                    ),
                    "Link": st.column_config.LinkColumn("Feed URL", width="medium", display_text="🔗 Visit Feed")
                },
                hide_index=True,
                use_container_width=True
            )

    # ----------------------------------------------------
    # TAB 2: ARTICLES BY SOURCE
    # ----------------------------------------------------
    with tab_articles:
        col_s_head, col_s_reset = st.columns([4, 1])
        with col_s_head:
            st.subheader("Browse Articles Grouped by Source")
        with col_s_reset:
            if st.button("🔄 Reset Filter", key="btn_reset_source_filter"):
                if "sel_source_filter" in st.session_state:
                    del st.session_state["sel_source_filter"]
                st.rerun()

        active_source_names = sorted(list(article_counts_by_source.keys()))
        selected_source = st.selectbox(
            "Select Source:",
            ["All Sources"] + active_source_names,
            key="sel_source_filter"
        )

        if selected_source == "All Sources":
            for s_name in active_source_names:
                source_articles = [a for a in articles if a.get('source_name') == s_name]
                if not source_articles:
                    continue

                st.markdown(f"### 📌 {s_name} ({len(source_articles)} articles)")
                for article in source_articles[:8]:
                    theme = article.get('theme', 'Unknown')
                    theme_color = THEME_COLORS.get(theme, '#666')

                    with st.expander(f"📰 {article.get('title', 'Untitled')[:85]}..."):
                        st.markdown(f"**Theme:** <span class='theme-pill' style='background-color: {theme_color};'>{theme}</span>", unsafe_allow_html=True)
                        st.markdown(f"**Published Date:** {article.get('published_date', 'Unknown')[:10]}")
                        if article.get('summary'):
                            st.markdown(f"**Summary:** {article['summary']}")
                        if article.get('link'):
                            st.markdown(f"[Read Article]({article['link']})")

                st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        else:
            source_articles = [a for a in articles if a.get('source_name') == selected_source]
            st.markdown(f"### 📌 {selected_source} ({len(source_articles)} articles)")

            for article in source_articles:
                theme = article.get('theme', 'Unknown')
                theme_color = THEME_COLORS.get(theme, '#666')

                with st.expander(f"📰 {article.get('title', 'Untitled')}"):
                    st.markdown(f"**Theme:** <span class='theme-pill' style='background-color: {theme_color};'>{theme}</span>", unsafe_allow_html=True)
                    st.markdown(f"**Published Date:** {article.get('published_date', 'Unknown')[:10]}")
                    if article.get('summary'):
                        st.markdown(f"**Summary:** {article['summary']}")
                    if article.get('link'):
                        st.markdown(f"[Read Article]({article['link']})")

    # ----------------------------------------------------
    # TAB 3: DEEP DIAGNOSTICS & ERROR ANALYSIS TOOL
    # ----------------------------------------------------
    with tab_diagnostics:
        st.subheader("🔬 Deep Source Health & Error Analysis Tool")
        st.markdown(
            "Perform live HTTP probes, RSS XML parsing checks, and BeautifulSoup DOM selector diagnostics on any registered feed. "
            "Surface plain-English root causes for HTTP 404s, malformed feeds, and 0-article extraction failures."
        )

        col_diag_sel, col_diag_btn = st.columns([3, 1])
        with col_diag_sel:
            diag_options = ["🔍 Analyze ALL Registered Sources"] + [s["name"] for s in SOURCES]
            selected_diag_source = st.selectbox(
                "Select Target Source for Live Inspection:",
                options=diag_options,
                key="sel_diag_source"
            )
        with col_diag_btn:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            run_diag = st.button("🚀 Run Live Diagnostics", type="primary", use_container_width=True)

        if run_diag:
            with st.spinner("Probing HTTP endpoints, verifying SSL/headers, parsing XML/DOM structures..."):
                if selected_diag_source == "🔍 Analyze ALL Registered Sources":
                    targets = SOURCES
                else:
                    targets = [s for s in SOURCES if s["name"] == selected_diag_source]

                diag_results = []
                with ThreadPoolExecutor(max_workers=8) as executor:
                    future_to_src = {executor.submit(diagnose_source, s): s for s in targets}
                    for future in as_completed(future_to_src):
                        try:
                            diag_results.append(future.result())
                        except Exception as exc:
                            src_info = future_to_src[future]
                            diag_results.append({
                                "name": src_info["name"],
                                "url": src_info["url"],
                                "type": src_info.get("type", "rss"),
                                "status_code": None,
                                "latency_ms": 0,
                                "content_type": "",
                                "items_found": 0,
                                "healthy": False,
                                "error_summary": "Exception",
                                "explanation": f"Probe error: {exc}",
                                "recommendation": "Inspect server connection."
                            })

                st.session_state["diag_results"] = diag_results
                st.success(f"✅ Completed diagnostic probe on {len(diag_results)} sources!")

        if "diag_results" in st.session_state:
            results = st.session_state["diag_results"]

            healthy_count = sum(1 for r in results if r["healthy"])
            unhealthy_count = len(results) - healthy_count

            col_h, col_uh = st.columns(2)
            col_h.metric("Healthy Probed Feeds", f"🟢 {healthy_count}")
            col_uh.metric("Sources Facing Errors / 0 Items", f"⚠️ {unhealthy_count}")

            st.divider()

            # Render Summary Dataframe
            df_diag = pd.DataFrame([
                {
                    "Source": r["name"],
                    "Verdict": "🟢 Healthy" if r["healthy"] else "❌ Error / Issue",
                    "HTTP Status": r["status_code"] if r["status_code"] is not None else "N/A",
                    "Latency": f"{r['latency_ms']} ms",
                    "Type": r["type"].upper(),
                    "Items Found": r["items_found"],
                    "Error Summary": r["error_summary"],
                    "URL": r["url"]
                }
                for r in results
            ])

            st.dataframe(
                df_diag,
                column_config={
                    "Source": st.column_config.TextColumn("Source Name", width="medium"),
                    "Verdict": st.column_config.TextColumn("Verdict", width="small"),
                    "HTTP Status": st.column_config.TextColumn("Status", width="small"),
                    "Latency": st.column_config.TextColumn("Latency", width="small"),
                    "Type": st.column_config.TextColumn("Type", width="small"),
                    "Items Found": st.column_config.NumberColumn("Items Found", width="small"),
                    "Error Summary": st.column_config.TextColumn("Error Summary", width="medium"),
                    "URL": st.column_config.LinkColumn("Endpoint URL", width="medium")
                },
                hide_index=True,
                use_container_width=True
            )

            # Detailed Breakdown of Issues
            unhealthy_results = [r for r in results if not r["healthy"]]
            if unhealthy_results:
                st.markdown("### ⚠️ Detailed Error Analysis & Troubleshooting Blueprint")
                for r in unhealthy_results:
                    with st.expander(f"❌ **{r['name']}** — {r['error_summary']} (HTTP {r['status_code'] or 'N/A'})"):
                        st.markdown(f"**Endpoint URL:** `{r['url']}`")
                        st.markdown(f"**Response Latency:** `{r['latency_ms']} ms` | **Content-Type:** `{r['content_type'] or 'N/A'}`")
                        st.markdown(f"**Root Cause Explanation:** {r['explanation']}")
                        st.markdown(f"**Actionable Recommendation:** {r['recommendation']}")


if __name__ == "__main__":
    main()
