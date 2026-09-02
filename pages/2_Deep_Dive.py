"""
AI Pulse - Deep Dive Page
Detailed analysis and article list for each thematic area.
"""

import streamlit as st
import pandas as pd

from config.themes import THEME_ORDER, THEME_COLORS
from core.design_system import apply_design_system, sanitize_summary_html
from core.shared_sidebar import render_sidebar_nav
from core.bg_refresher import check_and_show_bg_status
from core.provenance import render_provenance_chip, strip_fallback_banner

# Page configuration
st.set_page_config(
    page_title="Deep Dive - AI Pulse",
    page_icon="🔍",
    layout="wide"
)

# Apply central design system
apply_design_system()


def get_session_data():
    """Get data from session state."""
    if 'themed_articles' not in st.session_state or 'summaries' not in st.session_state:
        st.switch_page("app.py")
        st.stop()

    return st.session_state.themed_articles, st.session_state.summaries


def main() -> None:
    """Main deep dive page."""
    themed_articles, summaries = get_session_data()

    # Top of page alert if background update finished
    check_and_show_bg_status()

    # Sidebar Navigation
    render_sidebar_nav()

    # Header
    st.title("🔍 Thematic Deep Dive")
    st.markdown("### Granular Analysis & Article Explorer")
    st.divider()

    # Theme selector
    col_sel, col_export = st.columns([3, 1])
    with col_sel:
        selected_theme = st.selectbox(
            "Select Strategic Theme",
            THEME_ORDER,
            index=0,
            key="deep_dive_theme_select"
        )
    with col_export:
        # Multi-theme export support
        all_export_rows = []
        for th, arts in themed_articles.items():
            for a in arts:
                src = a.get("source_name") or a.get("source") or "Unknown Source"
                pub = a.get("published_date") or a.get("published_at") or a.get("published") or ""
                if pub and len(pub) >= 10:
                    pub = pub[:10]
                all_export_rows.append({
                    "Theme": th,
                    "Title": a.get("title", ""),
                    "Source": src,
                    "URL": a.get("link", ""),
                    "Published": pub
                })
        if all_export_rows:
            df_all = pd.DataFrame(all_export_rows)
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            st.download_button(
                "📥 Export All Themes CSV",
                df_all.to_csv(index=False).encode("utf-8"),
                f"ai_pulse_all_themes.csv",
                "text/csv",
                key="btn_export_all_csv",
                help="Export all tracked articles across all themes as CSV."
            )

    theme_color = THEME_COLORS.get(selected_theme, '#1f77b4')
    theme_summary = summaries.get(selected_theme, {})
    theme_articles = themed_articles.get(selected_theme, [])

    if len(theme_articles) < 3:
        st.warning(f"⚠️ Limited coverage this period with only {len(theme_articles)} articles for this theme.")

    st.divider()

    # Summary header tile
    col_header, col_count = st.columns([3, 1])
    with col_header:
        st.markdown(f"<h2 style='color: {theme_color}; margin-top:0;'>{selected_theme}</h2>", unsafe_allow_html=True)
        st.markdown(render_provenance_chip(theme_summary), unsafe_allow_html=True)
    with col_count:
        st.markdown(f"""
        <div class="metric-card" style="text-align: center; border-color: {theme_color}40 !important;">
            <div style="font-size: 30px; font-weight: bold; color: {theme_color};">{len(theme_articles)}</div>
            <div class="card-meta">Articles Tracked</div>
        </div>
        """, unsafe_allow_html=True)

    # On-Demand Gemini Summarisation Panel (Theme by Theme, User Initiated)
    with st.expander("✨ **On-Demand Gemini LLM Synthesis** (Upgrade Non-LLM Summary)", expanded=False):
        st.markdown(
            "If the default Non-LLM extractive summary is insufficient, trigger on-demand generative "
            "synthesis for **this theme only** using Google Gemini API."
        )
        from config.settings import GEMINI_AVAILABLE_MODELS, GEMINI_MODEL
        from core.summariser import generate_gemini_theme_summary
        from core.gemini_client import GeminiQuotaError, GeminiClientError

        c_model, c_btn = st.columns([2, 2])
        with c_model:
            selected_gemini_model = st.selectbox(
                "Gemini Model ID:",
                options=GEMINI_AVAILABLE_MODELS,
                index=GEMINI_AVAILABLE_MODELS.index(GEMINI_MODEL) if GEMINI_MODEL in GEMINI_AVAILABLE_MODELS else 0,
                key=f"gemini_model_select_{selected_theme}"
            )
        with c_btn:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            trigger_gemini = st.button(
                f"🚀 Synthesize '{selected_theme[:22]}...' with Gemini",
                key=f"btn_gemini_{selected_theme}",
                type="primary",
                width='stretch'
            )

        if trigger_gemini:
            if not theme_articles:
                st.warning("Cannot synthesize: No tracked articles available for this theme.")
            else:
                with st.spinner(f"Synthesizing '{selected_theme}' with Google {selected_gemini_model}..."):
                    try:
                        new_gemini_summary = generate_gemini_theme_summary(
                            selected_theme,
                            theme_articles,
                            model=selected_gemini_model
                        )
                        # Update session state
                        st.session_state.summaries[selected_theme] = new_gemini_summary

                        # Update local history.json
                        try:
                            import json
                            from core.history_manager import load_full_history, HISTORY_JSON
                            history_data = load_full_history()
                            if history_data:
                                latest_ts = sorted(history_data.keys(), reverse=True)[0]
                                if "summaries" in history_data[latest_ts]:
                                    history_data[latest_ts]["summaries"][selected_theme] = new_gemini_summary
                                    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
                                        json.dump(history_data, f, indent=2, ensure_ascii=False)
                        except Exception:
                            pass

                        # Update Supabase if available
                        try:
                            from core.supabase_client import get_supabase_manager
                            supabase = get_supabase_manager()
                            if supabase.is_available():
                                latest_run = supabase.get_latest_run()
                                if latest_run:
                                    supabase.save_theme_summary(latest_run["id"], selected_theme, new_gemini_summary, len(theme_articles))
                        except Exception:
                            pass

                        st.success(f"✅ Successfully synthesized '{selected_theme}' with Gemini {selected_gemini_model}!")
                        st.rerun()

                    except GeminiQuotaError as q_err:
                        suggested_models = [m for m in GEMINI_AVAILABLE_MODELS if m != selected_gemini_model]
                        st.error(
                            f"⚠️ **Google Gemini Quota Limit Reached (HTTP 429)** for model **`{selected_gemini_model}`**.\n\n"
                            f"💡 **Recommendation:** Please switch to another model ID (such as **`{suggested_models[0]}`** or **`{suggested_models[1]}`**) "
                            f"from the dropdown above and click Synthesize again."
                        )
                    except GeminiClientError as c_err:
                        st.error(f"❌ Gemini Synthesis Failed: {c_err}")
                    except Exception as err:
                        st.error(f"❌ Unexpected Error: {err}")

    # What is happening
    st.subheader("📰 The Signal (What is Happening)")
    if theme_summary.get('what_is_happening'):
        cleaned_signal = strip_fallback_banner(theme_summary['what_is_happening'])
        st.markdown(sanitize_summary_html(cleaned_signal))
    else:
        st.info("No summary available for this theme.")

    st.divider()

    # Strategic Significance & Impact
    st.subheader("🎯 Strategic Significance & Impact")
    has_eng = bool(theme_summary.get('engineering_tradeoffs') and theme_summary['engineering_tradeoffs'] != "No engineering tradeoffs analyzed.")
    has_prod = bool(theme_summary.get('product_impact') and theme_summary['product_impact'] != "No product impact analyzed.")

    if has_eng or has_prod:
        col_eng, col_prod = st.columns(2)
        with col_eng:
            st.markdown("##### 🛠️ Engineering Blueprint")
            st.info(theme_summary.get('engineering_tradeoffs', 'No engineering tradeoffs analyzed.'))
        with col_prod:
            st.markdown("##### 💼 Product Feasibility")
            st.success(theme_summary.get('product_impact', 'No product impact analyzed.'))
    else:
        if theme_summary.get('why_it_matters'):
            st.markdown(theme_summary['why_it_matters'])
        else:
            st.info("No analysis available.")

    st.divider()

    # What to watch
    st.subheader("👁️ Watchlist (Future Outlook)")
    if theme_summary.get('what_to_watch'):
        watch_text = theme_summary['what_to_watch']
        if '\n' in watch_text:
            for line in watch_text.split('\n'):
                stripped_line = line.strip()
                if stripped_line:
                    # Strip leading markdown list bullets or numbers to avoid nested double bullets
                    if stripped_line.startswith(('-', '*', '+')):
                        stripped_line = stripped_line.lstrip('-*+ \t')
                    import re
                    stripped_line = re.sub(r'^\d+\.\s*', '', stripped_line)
                    st.markdown(f"- {stripped_line}")
        else:
            st.markdown(watch_text)
    else:
        st.info("No items to watch.")

    st.divider()

    # Further reading
    st.subheader("📚 Cited Sources & Further Reading")
    if theme_summary.get('further_reading'):
        st.markdown(theme_summary['further_reading'])
    else:
        st.info("No further reading suggestions available.")

    st.divider()

    # Article table & CSV Download
    st.subheader("📋 Tracked Source Articles")

    if theme_articles:
        rows = []
        for a in theme_articles:
            src = a.get("source_name") or a.get("source") or "Unknown Source"
            pub = a.get("published_date") or a.get("published_at") or a.get("published") or ""
            if pub and len(pub) >= 10:
                pub = pub[:10]
            rows.append({
                "Title": a.get("title", ""),
                "Source": src,
                "Published": pub,
                "Link": a.get("link", "")
            })
        df_articles = pd.DataFrame(rows)

        st.dataframe(
            df_articles,
            column_config={
                "Title": st.column_config.TextColumn("Title", width="medium"),
                "Source": st.column_config.TextColumn("Source", width="small"),
                "Published": st.column_config.TextColumn("Published", width="small"),
                "Link": st.column_config.LinkColumn("Link", width="medium")
            },
            hide_index=True,
            width='stretch'
        )

        csv = df_articles.to_csv(index=False).encode('utf-8')
        st.download_button(
            label=f"📥 Download {selected_theme} CSV",
            data=csv,
            file_name=f"ai_pulse_{selected_theme.lower().replace(' ', '_')}.csv",
            mime="text/csv",
            key="btn_export_single_csv"
        )
    else:
        st.info("No articles found for this theme.")


if __name__ == "__main__":
    main()
