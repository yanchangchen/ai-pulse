"""
AI Pulse - Sage Page
The dedicated conversational research analyst: chat with persistent
conversations, research-scope control, and an opt-in deep-research
pipeline (Scout -> Fact-Checker -> Analyst, Phase 7).

Extracted from the Memory Wiki page (5_History) 2026-11-28 so Sage has a
focused home; Memory Wiki keeps the Timeline / Compare Runs / monthly
rollups.  See docs/SAGE.md for the full reference.
"""

import streamlit as st
from datetime import date, datetime, timedelta

from core.shared_sidebar import render_sidebar_nav
from core.supabase_client import get_supabase_manager
from core.design_system import apply_design_system

# Page configuration
st.set_page_config(
    page_title="Sage - AI Pulse",
    page_icon="🔮",
    layout="wide"
)

apply_design_system()


def _render_sage_article_evidence(articles):
    """Pillar D evidence popover: an accordion under a Sage reply listing the
    article briefs that grounded it (Phase 2.2).

    Each entry links to the original article (links were sanitised to
    http(s) URLs in ``core/sage_agent.py``) and shows the exact snippet Sage
    saw, so every claim is inspectable at the source.  Renders nothing when
    the reply has no article evidence (e.g. pre-2.2 messages).
    """
    if not articles:
        return
    items = []
    for a in articles:
        title = (a.get("title") or "Untitled article").strip()
        # Escape brackets so a title containing them can't break the
        # Markdown link syntax below.
        title = title.replace("[", "\\[").replace("]", "\\]")
        meta = " · ".join(
            part for part in (a.get("source_name"), a.get("_date")) if part
        )
        snippet = (a.get("snippet") or "").strip()
        link = a.get("link")
        head = f"[{title}]({link})" if link else title
        item = f"- **{head}**" + (f" — {meta}" if meta else "")
        if snippet:
            item += f"\n  \n  {snippet}"
        items.append(item)
    with st.expander(f"📰 Article Evidence ({len(articles)})", expanded=False):
        st.markdown("\n\n".join(items))


def _render_pipeline_detail(pipe):
    """Phase 7: the deep-research stage breakdown under a reply."""
    sq = pipe.get("sub_queries") or []
    claims = pipe.get("unsupported_claims") or []
    if not pipe.get("factcheck_ok"):
        fc = "checker unavailable (draft published)"
    elif claims:
        fc = f"{len(claims)} claim(s) flagged"
    else:
        fc = "clean"
    rev = " · revised" if pipe.get("revised") else ""
    total_ms = sum(s.get("latency_ms", 0) for s in pipe.get("stages", []))
    st.caption(
        f"🔬 Deep research: {len(sq)} sub-queries · fact-check {fc}{rev} · "
        f"{total_ms / 1000:.1f}s pipeline"
    )
    with st.expander("🔬 Pipeline detail", expanded=False):
        st.markdown("**Scout sub-queries:**")
        for q in sq:
            st.markdown(f"- {q}")
        stages = pipe.get("stages", [])
        if stages:
            rows = "| stage | ok | latency | model |\n|---|---|---|---|\n"
            for s in stages:
                rows += (
                    f"| {s.get('stage', '')} | {'✅' if s.get('ok') else '⚠️'} "
                    f"| {s.get('latency_ms', 0) / 1000:.1f}s | {s.get('model') or '—'} |\n"
                )
            st.markdown(rows)
        if claims:
            st.markdown("**Fact-checker flagged:**")
            for c in claims:
                st.markdown(f"- {c.get('claim', '')} — _{c.get('reason', '')}_")
        ids = []
        if pipe.get("correlation_id"):
            ids.append(f"correlation `{pipe['correlation_id']}`")
        if pipe.get("trace_id"):
            ids.append(f"trace `{pipe['trace_id'][:12]}…`")
        if ids:
            st.caption(" · ".join(ids) + " (llm_calls / Langfuse)")


def _render_sage(supabase):
    """The Sage chat interface: conversations, scope control, fast/deep modes."""
    from core.sage_agent import SAGE_INTRO, build_wiki_context, chat_with_sage
    from core.sage_pipeline import chat_with_sage_deep
    from core.evaluator import record_sage_faithfulness
    from core.sage_conversations import (
        list_saved_conversations,
        get_conversation,
        save_conversation,
        delete_conversation,
        export_conversation_markdown,
    )
    from config.themes import THEME_ORDER

    # Sage intro banner
    st.markdown(f"""
    <div class="sage-intro">
        <h3>🔮 Sage — AI Research Analyst</h3>
        <p>"{SAGE_INTRO}"</p>
    </div>
    """, unsafe_allow_html=True)

    # Initialise session state keys
    if "sage_messages" not in st.session_state:
        st.session_state.sage_messages = []
    if "sage_current_conv_id" not in st.session_state:
        st.session_state.sage_current_conv_id = None
    if "sage_conv_title" not in st.session_state:
        st.session_state.sage_conv_title = ""
    if "sage_period_preset" not in st.session_state:
        st.session_state.sage_period_preset = "📅 Last 30 Days"
    if "sage_selected_theme" not in st.session_state:
        st.session_state.sage_selected_theme = "All Themes"

    today = date.today()

    # -------------------------------------------------------------------------
    # 1. Conversation Management Bar
    # -------------------------------------------------------------------------
    saved_convs = list_saved_conversations()

    conv_options = {"__active__": f"💬 Current Session ({len(st.session_state.sage_messages)} msgs)"}
    for c in saved_convs:
        title_snippet = c["title"][:38] + ("…" if len(c["title"]) > 38 else "")
        time_snippet = c["updated_at"][:10] if c.get("updated_at") else ""
        conv_options[c["id"]] = f"📁 {title_snippet} ({c['message_count']} msgs · {time_snippet})"

    active_id = st.session_state.sage_current_conv_id or "__active__"
    if active_id not in conv_options:
        active_id = "__active__"

    col_conv_select, col_new, col_save, col_export, col_del = st.columns([4, 1.3, 1.4, 1.4, 1.1])

    with col_conv_select:
        selected_conv_key = st.selectbox(
            "Conversation History",
            options=list(conv_options.keys()),
            format_func=lambda k: conv_options.get(k, k),
            index=list(conv_options.keys()).index(active_id),
            label_visibility="collapsed",
            key="sage_thread_selector",
        )
        if selected_conv_key != active_id and selected_conv_key != "__active__":
            loaded = get_conversation(selected_conv_key)
            if loaded:
                st.session_state.sage_messages = loaded.get("messages", [])
                st.session_state.sage_current_conv_id = loaded.get("id")
                st.session_state.sage_conv_title = loaded.get("title", "")
                if loaded.get("theme_filter"):
                    st.session_state.sage_selected_theme = loaded["theme_filter"]
                if loaded.get("period_label"):
                    st.session_state.sage_period_preset = loaded["period_label"]
                st.rerun()

    with col_new:
        if st.button("➕ New", key="sage_btn_new", help="Start a new blank conversation"):
            st.session_state.sage_messages = []
            st.session_state.sage_current_conv_id = None
            st.session_state.sage_conv_title = ""
            st.rerun()

    with col_save:
        can_save = len(st.session_state.sage_messages) > 0
        save_label = "💾 Update" if st.session_state.sage_current_conv_id else "💾 Save"
        if st.button(save_label, key="sage_btn_save", disabled=not can_save, help="Save conversation to persistent history"):
            saved_id = save_conversation(
                messages=st.session_state.sage_messages,
                conv_id=st.session_state.sage_current_conv_id,
                title=st.session_state.sage_conv_title or None,
                theme_filter=st.session_state.sage_selected_theme,
                period_label=st.session_state.sage_period_preset,
            )
            st.session_state.sage_current_conv_id = saved_id
            st.toast("✅ Conversation saved!", icon="💾")
            st.rerun()

    with col_export:
        can_export = len(st.session_state.sage_messages) > 0
        if can_export:
            export_title = st.session_state.sage_conv_title or "Sage_Analysis"
            md_text = export_conversation_markdown(
                title=export_title,
                messages=st.session_state.sage_messages,
                theme_filter=st.session_state.sage_selected_theme,
                period_label=st.session_state.sage_period_preset,
            )
            st.download_button(
                label="📥 Export",
                data=md_text,
                file_name=f"sage_report_{datetime.now().strftime('%Y%m%d_%H%M')}.md",
                mime="text/markdown",
                help="Download conversation as Markdown report",
                key="sage_btn_export",
            )
        else:
            st.button("📥 Export", disabled=True, key="sage_btn_export_disabled")

    with col_del:
        is_saved = bool(st.session_state.sage_current_conv_id)
        if st.button("🗑️", key="sage_btn_delete", disabled=not is_saved, help="Delete active saved conversation"):
            delete_conversation(st.session_state.sage_current_conv_id)
            st.session_state.sage_messages = []
            st.session_state.sage_current_conv_id = None
            st.session_state.sage_conv_title = ""
            st.toast("Conversation deleted.", icon="🗑️")
            st.rerun()

    # -------------------------------------------------------------------------
    # 2. Scope & Research Filters (Period, Theme, Source) + Deep-research mode
    # -------------------------------------------------------------------------
    with st.expander("🔭 **Research Scope & Historical Filters**", expanded=False):
        c_period, c_theme, c_src = st.columns(3)

        with c_period:
            period_options = [
                "📅 Last 30 Days",
                "⚡ Last 7 Days",
                f"🍂 Current Month ({today.strftime('%B %Y')})",
                "♾️ Full Archive (All Available Dates)",
                "🗓️ Custom Date Range",
            ]
            current_preset_idx = period_options.index(st.session_state.sage_period_preset) if st.session_state.sage_period_preset in period_options else 0
            selected_preset = st.selectbox(
                "Time Period",
                period_options,
                index=current_preset_idx,
                key="sage_period_select_input",
            )
            st.session_state.sage_period_preset = selected_preset

        with c_theme:
            all_themes_opt = ["All Themes"] + THEME_ORDER
            theme_choice = st.selectbox(
                "Focus Theme",
                all_themes_opt,
                index=all_themes_opt.index(st.session_state.sage_selected_theme) if st.session_state.sage_selected_theme in all_themes_opt else 0,
                key="sage_theme_select_input",
            )
            st.session_state.sage_selected_theme = theme_choice

        with c_src:
            sources = ["All Sources"]
            if supabase and supabase.is_available():
                unique_sources = supabase.get_unique_sources()
                if unique_sources:
                    sources.extend(unique_sources)
            selected_source = st.selectbox("Source Filter", sources, key="sage_source_select_input")

        # Resolve date boundaries based on preset
        custom_c1, custom_c2 = st.columns(2)
        if selected_preset == "⚡ Last 7 Days":
            resolved_date_from = (today - timedelta(days=7)).strftime("%Y-%m-%d")
            resolved_date_to = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        elif selected_preset == "📅 Last 30 Days":
            resolved_date_from = (today - timedelta(days=30)).strftime("%Y-%m-%d")
            resolved_date_to = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        elif selected_preset.startswith("🍂 Current Month"):
            resolved_date_from = today.replace(day=1).strftime("%Y-%m-%d")
            resolved_date_to = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        elif selected_preset == "♾️ Full Archive (All Available Dates)":
            resolved_date_from = None
            resolved_date_to = None
        else:  # Custom Range
            with custom_c1:
                start_d = st.date_input("From Date", value=today - timedelta(days=60), key="sage_custom_start")
            with custom_c2:
                end_d = st.date_input("To Date", value=today, key="sage_custom_end")
            resolved_date_from = start_d.strftime("%Y-%m-%d")
            resolved_date_to = (end_d + timedelta(days=1)).strftime("%Y-%m-%d")

    # If expander was not rendered/evaluated, fall back to resolved dates
    if "resolved_date_from" not in locals():
        if st.session_state.sage_period_preset == "⚡ Last 7 Days":
            resolved_date_from = (today - timedelta(days=7)).strftime("%Y-%m-%d")
            resolved_date_to = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        elif st.session_state.sage_period_preset == "📅 Last 30 Days":
            resolved_date_from = (today - timedelta(days=30)).strftime("%Y-%m-%d")
            resolved_date_to = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        elif st.session_state.sage_period_preset.startswith("🍂 Current Month"):
            resolved_date_from = today.replace(day=1).strftime("%Y-%m-%d")
            resolved_date_to = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        elif st.session_state.sage_period_preset == "♾️ Full Archive (All Available Dates)":
            resolved_date_from = None
            resolved_date_to = None
        else:
            resolved_date_from = (today - timedelta(days=60)).strftime("%Y-%m-%d")
            resolved_date_to = (today + timedelta(days=1)).strftime("%Y-%m-%d")

    active_theme_filter = None if st.session_state.sage_selected_theme == "All Themes" else st.session_state.sage_selected_theme
    active_source_filter = None if (not 'selected_source' in locals() or selected_source == "All Sources") else selected_source

    # Phase 7: opt-in deep-research pipeline (default off — the fast single
    # call stays the default for casual questions).  Persisted per session.
    deep_mode = st.toggle(
        "🔬 Deep research (Scout → Fact-Checker → Analyst)",
        value=False,
        key="sage_deep_mode",
        help="Multi-agent pipeline: the Scout plans sub-queries, retrieval runs "
             "per sub-query, the Analyst drafts, and a Fact-Checker validates "
             "every statement against the context (one bounded revision). "
             "~30-45s per reply vs ~10-20s fast mode. Every stage is traced.",
    )

    # Render Active Scope Badge
    theme_badge = active_theme_filter or "All Themes"
    date_display = f"{resolved_date_from} → {resolved_date_to}" if resolved_date_from else "Full Archive (Chronological)"
    mode_badge = " · 🔬 Deep" if deep_mode else ""
    st.markdown(
        f'<div style="font-size:13px; color:#9aa0a6; padding: 6px 12px; border-radius: 6px; background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08); margin-bottom: 12px;">'
        f'🔭 <b>Active Scope:</b> <code>{st.session_state.sage_period_preset}</code> ({date_display}) &nbsp;|&nbsp; '
        f'<b>Theme:</b> <code>{theme_badge}</code> &nbsp;|&nbsp; '
        f'<b>Source:</b> <code>{active_source_filter or "All"}</code>{mode_badge}'
        f'</div>',
        unsafe_allow_html=True,
    )

    # If empty conversation, show starter prompts
    if not st.session_state.sage_messages:
        st.markdown(
            """
            <div style="background: rgba(255,255,255,0.02); border: 1px dashed #3c4043; border-radius: 8px; padding: 14px 18px; margin: 12px 0 16px 0;">
                <span style="font-size: 13px; color: #8ab4f8; font-weight: 600;">💡 SUGGESTED QUESTIONS:</span>
                <ul style="font-size: 13px; color: #bdc1c6; margin: 8px 0 0 16px; padding: 0;">
                    <li><i>"What were the most important developments across September 2026?"</i></li>
                    <li><i>"How did reasoning models and latent reasoning evolve recently?"</i></li>
                    <li><i>"When did we first see autonomous coding agent benchmarks like Terminal-Bench?"</i></li>
                    <li><i>"What are the major engineering tradeoffs reported in recent model deployments?"</i></li>
                </ul>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Render conversation history
    for msg in st.session_state.sage_messages:
        avatar = "🔮" if msg["role"] == "assistant" else "👤"
        with st.chat_message(msg["role"], avatar=avatar):
            st.markdown(msg["content"])
            if msg["role"] == "assistant":
                _render_sage_article_evidence(msg.get("articles"))

    # Chat input
    user_input = st.chat_input("Ask Sage anything about AI trends...")

    if user_input:
        # Display user message immediately
        with st.chat_message("user", avatar="👤"):
            st.markdown(user_input)
        st.session_state.sage_messages.append({"role": "user", "content": user_input})

        # Build context and get Sage's response
        with st.chat_message("assistant", avatar="🔮"):
            spinner_text = (
                "Sage is researching (Scout → Fact-Checker → Analyst)..."
                if deep_mode else "Sage is analyzing the archive..."
            )
            with st.spinner(spinner_text):
                wiki_ctx = build_wiki_context(
                    supabase=supabase,
                    question=user_input,
                    theme_filter=active_theme_filter,
                    date_from=resolved_date_from,
                    date_to=resolved_date_to,
                    source_filter=active_source_filter,
                )

                if deep_mode:
                    sage_result = chat_with_sage_deep(
                        messages=st.session_state.sage_messages,
                        wiki_context=wiki_ctx,
                        supabase=supabase,
                        theme_filter=active_theme_filter,
                        date_from=resolved_date_from,
                        date_to=resolved_date_to,
                        source_filter=active_source_filter,
                    )
                else:
                    sage_result = chat_with_sage(
                        messages=st.session_state.sage_messages,
                        wiki_context=wiki_ctx,
                    )
                sage_response = sage_result["response"]

            st.markdown(sage_response)

            # Engine provenance caption (gateway fallback chain + latency)
            prov = sage_result.get("provenance") or {}
            if prov.get("model"):
                fallback_tag = " · fallback" if prov.get("fallback_used") else ""
                st.caption(
                    f"🧠 Engine: {prov.get('provider', '?')}/{prov.get('model')}"
                    f" · {prov.get('latency_ms', 0) / 1000:.1f}s{fallback_tag}"
                )

            # Phase 7 pipeline caption + stage breakdown (deep mode only)
            pipe = sage_result.get("pipeline") or {}
            if pipe.get("mode") == "deep":
                _render_pipeline_detail(pipe)

            stats = wiki_ctx if isinstance(wiki_ctx, dict) else {}
            rollup_note = ""
            if stats.get("tier3_block_count"):
                _months = stats.get("tier3_months") or []
                _mrange = (
                    f"{_months[0]}→{_months[-1]}" if len(_months) > 1
                    else (_months[0] if _months else "")
                )
                rollup_note = (
                    f" · 🗓️ **{stats['tier3_block_count']}** monthly rollups ({_mrange})"
                )
            if stats.get("date_count"):
                article_note = (
                    f" · **{stats['article_count']}** article evidence"
                    if stats.get("article_count")
                    else ""
                )
                st.caption(
                    f"📚 Grounded in **{stats['run_count']}** summaries across "
                    f"**{stats['date_count']}** dates ({stats['date_range']})"
                    f"{article_note}{rollup_note}"
                )
            elif stats.get("tier3_block_count"):
                # Rollup-only context (Tier-2 window empty, Tier-3 reached).
                st.caption(f"📚 Grounded in{rollup_note}")
            elif stats.get("article_count"):
                # No theme summaries matched, but article evidence did.
                st.caption(
                    f"📰 Grounded in **{stats['article_count']}** article evidence "
                    f"(no theme summaries matched the selected filters)"
                )
            elif stats.get("run_count") == 0:
                st.caption("ℹ️ *Notice: No wiki records matched the selected period/theme filters.*")

            # Evidence popover for this reply — deep mode rebuilt the evidence
            # section from sub-queries, so prefer the pipeline's merged list.
            reply_articles = sage_result.get("articles") or stats.get("articles")
            _render_sage_article_evidence(reply_articles)

            # Phase 2 gate: judge this reply's faithfulness against the exact
            # context it was grounded in, fire-and-forget.  Deep mode judges
            # against the pipeline's rebuilt context, not the fast-path one.
            judge_context = sage_result.get("context") or stats.get("context")
            if prov.get("method") == "llm" and isinstance(judge_context, str):
                record_sage_faithfulness(
                    question=user_input,
                    answer=sage_response,
                    context=judge_context,
                    conversation_id=st.session_state.sage_current_conv_id,
                    answer_model=prov.get("model"),
                    answer_mode="deep" if deep_mode else "fast",
                )

        assistant_msg = {"role": "assistant", "content": sage_response}
        if reply_articles:
            assistant_msg["articles"] = reply_articles
        st.session_state.sage_messages.append(assistant_msg)

        # Auto-update saved conversation thread if one is active
        if st.session_state.sage_current_conv_id:
            save_conversation(
                messages=st.session_state.sage_messages,
                conv_id=st.session_state.sage_current_conv_id,
                title=st.session_state.sage_conv_title or None,
                theme_filter=st.session_state.sage_selected_theme,
                period_label=st.session_state.sage_period_preset,
            )

        st.rerun()


def main() -> None:
    from core.bg_refresher import check_and_show_bg_status

    check_and_show_bg_status()

    st.title("🔮 Sage")
    st.markdown("### Conversational AI Research Analyst")
    st.divider()

    render_sidebar_nav(current="sage")

    supabase = get_supabase_manager()
    if not supabase.is_available():
        st.info(
            "ℹ️ Sage needs the Supabase archive to ground its answers. "
            "Connect Supabase (see the Setup guide) to unlock the research analyst."
        )
        st.page_link("app.py", label="Back to Dashboard", icon="🏠")
        return

    _render_sage(supabase)


if __name__ == "__main__":
    main()