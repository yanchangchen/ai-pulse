"""
AI Pulse - Quality Evaluation Page
On-demand and weekly view of 7 quality metrics (Categoriser, Faithfulness, Uniqueness,
Grounding, Structural Compliance, Coverage, and Temporal Coherence) produced by
LLM judge agents and sub-millisecond deterministic checkers in core/evaluator.py.
"""

from __future__ import annotations

import time
from datetime import datetime

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

from config.settings import EVALUATION_JUDGE_MODELS, QUALITY_THRESHOLD
from config.themes import THEME_ORDER
from core.supabase_client import get_supabase_manager
from core.shared_sidebar import render_sidebar_nav
from core.bg_refresher import check_and_show_bg_status
from core.eval_controller import EvaluationRunner, load_checkpoint
from core.evaluator import consume_judge_events

from core.design_system import apply_design_system

st.set_page_config(page_title="Quality Evaluation - AI Pulse", page_icon="🔬", layout="wide")

# Apply central design system
apply_design_system()


# ---------------------------------------------------------------------------
# Copy-to-clipboard formatters
# ---------------------------------------------------------------------------

def _format_theme_suggestion_for_file(theme: str, items: list) -> str:
    """Format suggested keywords as a Python dict snippet ready to paste
    into ``config/themes.py`` under the ``THEMES`` definition.

    Example output::

        # THEMES["AI Applications & Architecture"] additions
        THEMES["AI Applications & Architecture"]["keywords"].update({
            "prompt injection": 3,
            "agentic mesh": 2,
            "vector index": 1,
        })
    """
    lines = [f'# THEMES["{theme}"] additions',
             f'THEMES["{theme}"]["keywords"].update({{']
    for it in items:
        term = (it.get("term") or "").replace('"', '\\"')
        weight = int(it.get("weight") or 2)
        lines.append(f'    "{term}": {weight},')
    lines.append("})")
    return "\n".join(lines)


def _format_watchlist_for_file(items: list) -> str:
    """Format suggested watchlist terms as CSV rows ready to paste into
    the ``## 1. SEARCH KEYWORDS`` table in ``watch.md``.  Category is
    inferred from the bracketed prefix embedded in the reason field.
    """
    lines = ["| Category | Keywords |", "|----------|----------|"]
    for it in items:
        reason = (it.get("reason") or "").strip()
        category = "(uncategorised)"
        if reason.startswith("[") and "]" in reason:
            category = reason[1:reason.index("]")].strip()
        # Drop the bracket prefix when showing the term list.
        raw_term = (it.get("term") or "").replace("|", "/").strip()
        if not category:
            category = "(uncategorised)"
        lines.append(f"| {category} | {raw_term} |")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
render_sidebar_nav()
check_and_show_bg_status()

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------
st.title("🔬 Quality Evaluation")
st.caption(
    "Reference-free quality scoring of the AI Pulse pipeline evaluating 7 quality metrics. "
    "Three LLM judge agents run concurrently via ThreadPoolExecutor + Semaphore(3): **Categoriser** (fresh theme re-classification), "
    "**Faithfulness** (fact-checking summary claims against theme-filtered source articles), and **Uniqueness** (pairwise overlap across themes and runs). "
    "Four sub-millisecond deterministic judges run alongside: **Grounding** (verifying further reading citations match source articles), "
    "**Structural Compliance** (section presence, sentence bounds, list formatting), **Coverage** (source article recall), and "
    "**Temporal Coherence** (week-over-week summary evolution)."
)

with st.expander("📖 Guide: How Evaluation Checks Work & In-App Remedy Actions", expanded=False):
    st.markdown("""
    ### ⚙️ Evaluation Suite Architecture
    The evaluation suite runs **7 automated checks** combining **3 concurrent LLM judge agents** (executing in parallel via `ThreadPoolExecutor` + `Semaphore(3)`) with **4 sub-millisecond deterministic judges**:

    - **3 Concurrent LLM Judges**: **Categoriser** (fresh theme re-classification sample), **Faithfulness** (fact-checking summary claims against source articles), and **Uniqueness** (pairwise summary overlap across themes and runs).
    - **4 Deterministic Judges**: **Grounding** (citation verification), **Structural Compliance** (section, sentence, and list formatting bounds), **Coverage** (source article recall), and **Temporal Coherence** (week-over-week summary evolution tracking).

    ---

    ### 📊 Layman & Technical Guide to the 7 Judge Checks

    | Metric | Judge Type | What It Checks (Layman Explanation) | How It Checks (Technical Method) | Target Threshold |
    |---|---|---|---|---|
    | **Categoriser Accuracy** | LLM-as-Judge | Are articles being sorted into the right themes? | Re-classifies a stratified sample of articles via LLM and compares predicted themes against active assignments. | ≥ 80% |
    | **Faithfulness Score** | LLM-as-Judge | Are the generated summaries truthful to the original articles without making things up? | Extracts claims from summary bullet points and uses LLM fact-checking against theme-filtered source article text. | ≥ 80% |
    | **Uniqueness Score** | Hybrid Heuristic + LLM | Are different theme summaries distinct, or are they repeating the same stories across themes/runs? | Performs Jaccard/cosine text overlap filtering; invokes LLM pairwise uniqueness judge when overlap is in ambiguous bands. | ≥ 80% |
    | **Grounding Score** | Deterministic | Do the "Further Reading" links point to real articles fetched in the run? | Cross-references cited titles/links in `further_reading` against the exact set of input source articles. When no citations exist (e.g. legacy rows saved before `supabase_migration_further_reading.sql`), the check reports **Skipped** rather than a vacuous 100%. | 100% |
    | **Structural Compliance** | Deterministic | Is the summary complete and properly shaped? | Validates all 5 persisted sections (`what_is_happening`, `engineering_tradeoffs`, `product_impact`, `what_to_watch`, `further_reading`) are non-empty and prose sections hold 3–7 sentences. Sections absent from a legacy schema are skipped, not failed. | 100% |
    | **Coverage Score** | Deterministic | Did the summary capture key information from all fetched articles, or did it ignore most of them? | Measures source article title/entity token recall across the generated theme summary. | ≥ 70% |
    | **Temporal Coherence** | Deterministic | Is the summary actually updating week-over-week, or is it echoing static past summaries? | Compares active summary against past run summaries to verify evolution claims and flag stale text repetition. | ≥ 75% |

    ---

    ### 🛠️ Guidance on In-App Remedy Actions (No Backend Code Edits Needed)

    When any metric falls below target, you do **not** need to edit backend code. Use the interactive controls built directly into this page:

    1. **⚡ 1-Click "Apply All Suggested Keywords"**:
       - When **Categoriser Accuracy** drops for a weak theme, scroll to the **Keyword Suggestions** section below and click **"⚡ Apply all suggested keywords to [Theme]"** to automatically persist missing terms.
    2. **🎛️ Theme Keyword Manager Tab**:
       - Switch to the **Theme Keyword Manager** standing control tab below to add new terms with custom weights (1–3) or delete weak keywords for any theme (persisted to `config/custom_keywords.json`).
    3. **⚙️ Faithfulness & Summariser Tuner Tab**:
       - Switch to the **Faithfulness & Summariser Tuner** standing control tab below to tune LLM parameters:
         - **Lower Temperature** (`0.1 – 0.2`) to suppress hallucinations.
         - **Reduce Max Output Tokens** to discourage ungrounded narrative filler.
         - **Enable Strict Anti-Hallucination Grounding Mode** to enforce source-article citation rules (persisted to `config/custom_settings.json`).
    4. **📌 Watchlist Term Suggestions**:
       - Copy suggested terms at the bottom of this page into `watch.md` to refine future RSS & web fetching precision.
    """)

# ---------------------------------------------------------------------------
# Supabase availability check
# ---------------------------------------------------------------------------
supabase = get_supabase_manager()
if not supabase.is_available():
    st.error(
        "🚫 **Supabase is not configured.**  Quality Evaluation requires Supabase "
        "to access historical runs.  Set `SUPABASE_URL` and `SUPABASE_KEY` in "
        "your environment and restart the app."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Evaluation history (lazy)
# ---------------------------------------------------------------------------
HISTORY_LIMIT = 12

@st.cache_data(ttl=60, show_spinner=False)
def _cached_history(_supabase, limit: int) -> pd.DataFrame:
    from core.evaluator import load_evaluation_history
    return load_evaluation_history(supabase=_supabase, limit=limit)

with st.spinner("Loading evaluation history…"):
    history_df = _cached_history(supabase, HISTORY_LIMIT)


def _render_evaluation_result(report, events: list) -> None:
    """Render a completed evaluation: success banner, classification gate
    waterfall, recommendations, auto-remediation log, keyword suggestions,
    and the history-cache invalidation.  Works for both a fresh run (report
    object in memory) and a resumed persist (report rebuilt from the
    checkpoint — events may then be empty)."""
    llm_calls = sum(1 for e in events if e["judge"] != "uniqueness" or e.get("latency_ms", 0) > 0)
    parse_fail = sum(1 for e in events if not e.get("parse_ok"))
    latencies = [e["latency_ms"] for e in events if e.get("latency_ms", 0) > 0]
    mean_lat = int(sum(latencies) / len(latencies)) if latencies else 0
    raw_m = getattr(report, "raw_metrics", {}) or {}
    judge_mode = raw_m.get("judge_selection", "all")

    if judge_mode == "deterministic":
        st.success(
            f"✅ Evaluation complete (4 Deterministic Judges Only) — "
            f"grounding {report.grounding_score:.0%}, "
            f"structural compliance {report.structural_compliance_score:.0%}, "
            f"coverage {report.coverage_score:.0%}, "
            f"temporal coherence {report.temporal_coherence_score:.0%}."
        )
    elif judge_mode == "llm":
        st.success(
            f"✅ Evaluation complete (3 LLM Judges Only) — "
            f"classifier {report.classifier_score:.0%}, "
            f"faithfulness {report.faithfulness_score:.0%}, "
            f"uniqueness {report.uniqueness_score:.0%}."
        )
    else:
        st.success(
            f"✅ Evaluation complete (All 7 Judges) — "
            f"classifier {report.classifier_score:.0%}, "
            f"faithfulness {report.faithfulness_score:.0%}, "
            f"uniqueness {report.uniqueness_score:.0%}, "
            f"grounding {report.grounding_score:.0%}, "
            f"coverage {report.coverage_score:.0%}."
        )

    judge_model_used = raw_m.get("judge_model", "auto")
    model_note = (
        f" • judge model: {judge_model_used}"
        if judge_mode != "deterministic" else ""
    )
    st.caption(
        f"Judge events: {len(events)} • LLM calls: ~{llm_calls} • "
        f"parse failures: {parse_fail} • mean latency: {mean_lat} ms"
        f"{model_note}"
    )

    # Render Classification Waterfall Gate Breakdown
    gates = raw_m.get("classifier_gates")
    if not gates:
        from core.classifier import get_latest_gate_stats
        gates = get_latest_gate_stats()

    if isinstance(gates, dict) and gates.get("total", 0) > 0:
        tot = gates["total"]
        p1 = gates.get("gate_1_keyword", 0)
        p2 = gates.get("gate_2_tfidf", 0)
        # NB: the classifier emits "gate_3_llm" (renamed from
        # gate_3_ollama in e1b7a7d) — reading the old key silently
        # rendered Pass 3 as 0.
        p3 = gates.get("gate_3_llm", 0)
        p4 = gates.get("gate_4_heuristic", 0)

        st.markdown("##### 🚪 Classification Pipeline Gate Breakdown")
        gc1, gc2, gc3, gc4 = st.columns(4)
        gc1.metric("Pass 1: Keyword", f"{p1}/{tot}", f"{p1/tot:.0%}" if tot else "0%")
        gc2.metric("Pass 2: TF-IDF", f"{p2}/{tot}", f"{p2/tot:.0%}" if tot else "0%")
        gc3.metric("Pass 3: LLM", f"{p3}/{tot}", f"{p3/tot:.0%}" if tot else "0%")
        gc4.metric("Pass 4: Heuristic", f"{p4}/{tot}", f"{p4/tot:.0%}" if tot else "0%")
    if report.recommendations:
        for rec in report.recommendations:
            if rec.startswith("⚠️"):
                st.warning(rec)
            else:
                st.success(rec)

    # Auto-remediation actions taken during this evaluation (opt-in).
    rem = (getattr(report, "raw_metrics", {}) or {}).get("auto_remediation") or {}
    if rem.get("applied") or rem.get("rolled_back"):
        st.markdown("#### 🤖 Auto-remediation actions")
        for a in rem.get("rolled_back", []):
            st.warning(
                f"↩️ Rolled back **{len(a.get('terms', []))} keyword(s)** for "
                f"**{a.get('theme')}** — score {a.get('current_score', 0):.0%} fell below "
                f"baseline {a.get('baseline_score', 0):.0%}: "
                f"`{', '.join(a.get('terms', []))}`"
            )
        for a in rem.get("applied", []):
            if a.get("action") == "apply_keywords":
                terms = a.get("terms", {})
                st.info(
                    f"⚡ Applied **{len(terms)} keyword(s)** to **{a.get('theme')}** "
                    f"(baseline {a.get('baseline_score', 0):.0%}, will roll back if the "
                    f"score drops next evaluation): "
                    f"`{', '.join(f'{t} (wt {w})' for t, w in terms.items())}`"
                )
            elif a.get("action") == "tune_summariser_faithfulness":
                st.info(
                    f"⚙️ Faithfulness remedy (score {a.get('trigger_score', 0):.0%}): "
                    f"strict grounding → **{a['new']['strict_faithfulness_mode']}**, "
                    f"temperature → **{a['new']['temperature']}**"
                )
            elif a.get("action") == "raise_max_tokens":
                st.info(
                    f"⚙️ Coverage remedy (score {a.get('trigger_score', 0):.0%}): "
                    f"max tokens {a['previous']['max_tokens']} → "
                    f"**{a['new']['max_tokens']}**"
                )

    kw = getattr(report, "keyword_suggestions", None)
    if isinstance(kw, dict) and (
        kw.get("theme_suggestions") or kw.get("watchlist_suggestions")
    ):
        st.markdown("#### 🧠 Suggested theme keywords")
        theme_map = kw.get("theme_suggestions") or {}
        for theme, items in theme_map.items():
            if not items:
                continue
            with st.expander(f"📁 {theme} — {len(items)} new keyword(s)", expanded=True):
                rows_text = "| term | weight | reason |\n|---|---|---|\n"
                kw_payload = {}
                for it in items:
                    term = (it.get("term") or "").replace("|", "\\|")
                    weight = int(it.get("weight") or 2)
                    reason = (it.get("reason") or "").replace("|", "\\|").replace("\n", " ")
                    rows_text += f"| {term} | {weight} | {reason} |\n"
                    if it.get("term"):
                        kw_payload[it["term"]] = weight
                st.markdown(rows_text)

                if st.button(f"⚡ Apply all {len(items)} suggested keywords to {theme}", key=f"btn_apply_kw_{theme}"):
                    from config.themes import add_keywords_to_theme
                    add_keywords_to_theme(theme, kw_payload)
                    st.toast(f"Applied {len(items)} keywords to {theme}!", icon="⚡")
                    st.success(f"Successfully added {len(items)} keywords to **{theme}** and persisted to custom keywords!")
                    st.rerun()

                st.code(
                    _format_theme_suggestion_for_file(theme, items),
                    language="text",
                )
        st.markdown("#### 📡 Suggested watchlist terms")
        watch = kw.get("watchlist_suggestions") or []
        if watch:
            with st.expander(f"📋 {len(watch)} new watchlist term(s)", expanded=True):
                rows_text = "| term | reason |\n|---|---|\n"
                for it in watch:
                    term = (it.get("term") or "").replace("|", "\\|")
                    reason = (it.get("reason") or "").replace("|", "\\|").replace("\n", " ")
                    rows_text += f"| {term} | {reason} |\n"
                st.markdown(rows_text)
                st.code(
                    _format_watchlist_for_file(watch),
                    language="text",
                )
        else:
            st.info("No new watchlist terms were suggested.")
    elif isinstance(kw, dict):
        st.caption(
            "ℹ️ No keyword or watchlist suggestions were produced (the "
            "LLM judges may have returned empty responses — check the "
            "live progress panel above or the `logs/app.log` file)."
        )

    # Invalidate the history cache so the new row appears immediately.
    _cached_history.clear()

# ---------------------------------------------------------------------------
# Run Evaluation & Settings Control Card
# ---------------------------------------------------------------------------
st.subheader("▶ Run Evaluation & Settings")
st.caption("Configure evaluation parameters below and trigger on-demand scoring. Results persist automatically to Supabase.")

with st.container():
    col_cfg1, col_cfg2, col_cfg3, col_cfg4 = st.columns([1.2, 1, 1, 1.2])

    with col_cfg1:
        judge_selection_label = st.selectbox(
            "🎯 Judges to Run",
            options=["All 7 Judges", "3 LLM Judges Only", "4 Deterministic Judges Only"],
            index=0,
            help="Select which judges to evaluate. '3 LLM Judges Only' runs categoriser, faithfulness, and uniqueness; '4 Deterministic Judges Only' runs zero-cost sub-millisecond rule checks without LLM calls.",
            key="cfg_judge_selection",
        )

    with col_cfg2:
        lookback_days = st.selectbox(
            "📅 Lookback Window",
            options=[1, 7, 14, 30],
            index=1,
            format_func=lambda d: f"{d} Day{'s' if d > 1 else ''}",
            help="How far back to look for trend_runs to evaluate.",
            key="cfg_lookback_days",
        )

    with col_cfg3:
        threshold = st.slider(
            "🎯 Threshold Score",
            min_value=0.50,
            max_value=0.99,
            value=QUALITY_THRESHOLD,
            step=0.01,
            format="%.2f",
            help="Scores below this threshold line trigger actionable recommendations.",
            key="cfg_threshold",
        )

    with col_cfg4:
        judge_model_label = st.selectbox(
            "🧠 Judge Model",
            options=["Auto (gateway routing)"] + list(EVALUATION_JUDGE_MODELS.values()),
            index=0,
            help=(
                "Pin every LLM judge call (categoriser, faithfulness, uniqueness, and "
                "keyword suggestions) to ONE model so scores stay comparable across "
                "evaluations — no cross-model fallback mid-run. 'Auto' uses the "
                "gateway's default Gemini-first routing chain."
            ),
            key="cfg_judge_model",
        )

judge_map = {
    "All 7 Judges": "all",
    "3 LLM Judges Only": "llm",
    "4 Deterministic Judges Only": "deterministic",
}
judge_selection = judge_map[judge_selection_label]

_judge_model_by_label = {label: key for key, label in EVALUATION_JUDGE_MODELS.items()}
judge_model = _judge_model_by_label.get(judge_model_label)

from core.auto_remediation import is_enabled as _auto_rem_enabled, set_enabled as _auto_rem_set

_auto_on = st.toggle(
    "🤖 Auto-apply remediations after each evaluation",
    value=_auto_rem_enabled(),
    help=(
        "Bounded and reversible. When metrics fall below the threshold: enables Strict "
        "Anti-Hallucination mode and lowers summariser temperature (Faithfulness), raises "
        "the token budget in 500-step increments up to 2500 (Coverage), and applies up to "
        "5 weight-capped keyword suggestions per weak theme (Classifier). Keyword applies "
        "are automatically rolled back if the theme's score drops in the next evaluation. "
        "Every action is recorded in the evaluation's raw metrics for audit."
    ),
    key="cfg_auto_remediation",
)
if _auto_on != _auto_rem_enabled():
    _auto_rem_set(_auto_on)
    st.toast("Auto-remediation " + ("enabled" if _auto_on else "disabled"), icon="🤖")

st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
# ---------------------------------------------------------------------------
# Run / pause / resume controls.  The worker thread lives in
# core.eval_controller.EvaluationRunner and survives page navigation and
# reruns — this page just reattaches to its status on every rerun.
# ---------------------------------------------------------------------------
runner_status = EvaluationRunner.get_status()
ckpt = load_checkpoint()

run_label = f"🚀 Run Evaluation Now ({judge_selection_label})"
if ckpt is not None and ckpt.get("status") != "completed":
    run_label += " — discards paused run"
run_now = st.button(
    run_label,
    type="primary",
    width="stretch",
    key="run_quality_eval_btn",
)

if run_now:
    if EvaluationRunner.is_running():
        st.toast("An evaluation is already running.", icon="⏳")
    else:
        started = EvaluationRunner.start(
            {
                "run_ids": [],
                "lookback_days": lookback_days,
                "threshold": threshold,
                "judge_selection": judge_selection,
                "judge_model": judge_model,
            },
            supabase=supabase,
        )
        if not started:
            st.error("Could not start the evaluation (see status above).")
        else:
            st.session_state["eval_progress"] = {"events": []}
            st.session_state["eval_result_rendered_for"] = None
    st.rerun()

progress_state = st.session_state.setdefault("eval_progress", {"events": []})


def _render_judge(col, name: str, items: list) -> None:
    done = len(items)
    ok = sum(1 for i in items if i.get("parse_ok"))
    scores = [i["score"] for i in items if i.get("score") is not None]
    mean_s = sum(scores) / len(scores) if scores else 0.0
    lat = [i["latency_ms"] for i in items if i.get("latency_ms")]
    mean_lat = sum(lat) / len(lat) if lat else 0
    with col:
        st.metric(name, f"{done} done", delta=f"ok {ok}/{done}")
        st.progress(min(1.0, done / max(1, 20)))
        st.caption(f"mean score {mean_s:.2f} • mean latency {int(mean_lat)} ms")


if runner_status["is_running"]:
    # --- Live progress panel (reattaches on every rerun) ---
    events = consume_judge_events()
    if events:
        progress_state["events"] = (progress_state["events"] + events)[-200:]
    evs = progress_state["events"]

    eff_status = runner_status["status"]
    if eff_status == "pausing":
        st.info("⏸ Pausing — the item currently being judged will finish and be saved first…")
    elif eff_status == "awaiting_db":
        st.warning(
            "🗄️ Database unavailable — the finished report is safe on disk and "
            "the final save retries automatically until the database recovers."
        )
    else:
        st.caption(
            "Auto-refreshing every ~2 s while judges run.  Progress is "
            "checkpointed — you can leave this page and come back."
        )

    st.markdown("#### 🔍 Live Judge Progress")
    col_cat, col_faith, col_uniq = st.columns(3)
    _render_judge(col_cat, "Categoriser", [e for e in evs if e["judge"] == "categoriser"])
    _render_judge(col_faith, "Faithfulness", [e for e in evs if e["judge"] == "faithfulness"])
    _render_judge(col_uniq, "Uniqueness", [e for e in evs if e["judge"] == "uniqueness"])
    recent = evs[-50:]
    if recent:
        df = pd.DataFrame(recent)
        df["ts"] = pd.to_datetime(df["ts"], unit="s").dt.strftime("%H:%M:%S")
        df = df[["ts", "judge", "run_id", "item_id", "latency_ms", "parse_ok", "score"]]
        st.dataframe(df, hide_index=True, width="stretch")

    if st.button("⏸ Pause Evaluation", key="btn_pause_eval"):
        EvaluationRunner.request_pause()
        st.toast("Pause requested — finishing the current item…", icon="⏸️")
    # Rerun-based refresh loop: re-renders every ~2 s so the Pause button
    # stays clickable (a blocking poll loop would freeze all widgets).
    time.sleep(2)
    st.rerun()
elif runner_status["status"] == "completed":
    report = runner_status.get("report")
    if (
        report is not None
        and st.session_state.get("eval_result_rendered_for") != report.generated_at
    ):
        _render_evaluation_result(report, progress_state["events"])
        st.session_state["eval_result_rendered_for"] = report.generated_at
elif ckpt is not None:
    ckpt_status = ckpt.get("status")
    if ckpt_status == "completed":
        st.success(
            f"✅ The last evaluation was saved to Supabase "
            f"(row {ckpt.get('db_row_id')})."
        )
        if st.button("🗑 Clear note", key="btn_clear_completed"):
            EvaluationRunner.discard()
            st.rerun()
    else:
        # Paused, interrupted (checkpoint "running" with no live thread =
        # process restart mid-run), or left waiting on the database.
        done = sum(
            sum(len(items) for items in runs.values())
            for runs in (ckpt.get("item_results") or {}).values()
        )
        cfg = ckpt.get("config") or {}
        phase = ckpt.get("phase") or "judges"
        phase_label = {
            "judges": "LLM judges", "keywords": "keyword suggestions",
            "persist": "final save",
        }.get(phase, phase)
        if ckpt_status == "running":
            title = "⚡ Interrupted evaluation found (the app restarted mid-run)."
        elif ckpt_status == "awaiting_db":
            title = "🗄️ Paused while waiting for the database to recover."
        else:
            title = "⏸ Paused evaluation found."
        st.warning(
            f"{title}  **{done} item(s)** already judged ({phase_label} phase). "
            f"Resuming continues with the original settings — lookback "
            f"{cfg.get('lookback_days')} day(s), judges "
            f"“{cfg.get('judge_selection')}”, model "
            f"{cfg.get('judge_model') or 'auto'} — and re-judges only what "
            f"isn't already checkpointed."
        )
        err = ckpt.get("error")
        if err:
            st.error(f"The previous attempt failed: {err}")
        rc1, rc2 = st.columns(2)
        if rc1.button("▶ Resume Evaluation", key="btn_resume_eval", type="primary", width="stretch"):
            if EvaluationRunner.resume(supabase=supabase):
                st.session_state["eval_progress"] = {"events": []}
                st.rerun()
        if rc2.button("🗑 Discard Paused Run", key="btn_discard_eval", width="stretch"):
            EvaluationRunner.discard()
            st.rerun()

if runner_status["status"] == "failed" and ckpt is None:
    st.error(f"❌ Evaluation failed: {runner_status.get('error')}")


# ---------------------------------------------------------------------------
# Latest scores
# ---------------------------------------------------------------------------
st.divider()
st.subheader("📊 Latest Scores")

if history_df.empty:
    st.info(
        "No quality evaluations yet.  Click **Run evaluation now** above to "
        "produce the first report."
    )
else:
    latest = history_df.iloc[-1]

    raw_m_latest = latest.get("raw_metrics", {}) or {}
    cat_sk = raw_m_latest.get("categoriser", {}).get("skipped", False)
    faith_sk = raw_m_latest.get("faithfulness", {}).get("skipped", False)
    uniq_sk = raw_m_latest.get("uniqueness", {}).get("skipped", False)
    ground_sk = raw_m_latest.get("grounding", {}).get("skipped", False)
    struct_sk = raw_m_latest.get("structural_compliance", {}).get("skipped", False)
    cov_sk = raw_m_latest.get("coverage", {}).get("skipped", False)
    temp_sk = raw_m_latest.get("temporal_coherence", {}).get("skipped", False)

    def _score_tile(col, label: str, score: float, threshold_val: float, skipped: bool = False) -> None:
        with col:
            if skipped:
                st.metric(label, "Skipped", delta="N/A (Skipped)")
                st.progress(0.0)
                st.markdown(
                    "<div style='height:6px;border-radius:3px;background:#555'></div>",
                    unsafe_allow_html=True,
                )
            else:
                pct = max(0.0, min(1.0, float(score)))
                ok = pct >= threshold_val
                color = "#1f9d55" if ok else "#c0392b"
                st.metric(label, f"{pct:.0%}", delta=f"threshold {threshold_val:.0%}")
                st.progress(pct)
                st.markdown(
                    f"<div style='height:6px;border-radius:3px;background:{color}'></div>",
                    unsafe_allow_html=True,
                )

    cat_raw_latest = raw_m_latest.get("categoriser", {}) or {}
    if cat_raw_latest.get("skipped") and (
        cat_raw_latest.get("quota_exceeded") or cat_raw_latest.get("no_gateway_providers")
    ):
        reason = (
            "The Ollama Cloud weekly rate limit was reached and no gateway provider "
            "could serve the judges."
            if cat_raw_latest.get("quota_exceeded")
            else "The Model Gateway has no providers configured (check GEMINI_API_KEY / "
                 "OLLAMA_API_KEY in secrets.toml or the environment)."
        )
        st.warning(
            f"**LLM Evaluation Judges Skipped**: {reason} "
            "Evaluation results were compiled using the **4 Deterministic Rule Checks** only.",
            icon="⚠️"
        )

    tab_llm_scores, tab_det_scores = st.tabs(["🤖 LLM Judge Metrics (3)", "⚡ Deterministic Rule Checks (4)"])

    with tab_llm_scores:
        c1, c2, c3 = st.columns(3)
        _score_tile(c1, "Classifier Accuracy", latest["classifier_score"], threshold, skipped=cat_sk)
        _score_tile(c2, "Faithfulness Score", latest["faithfulness_score"], threshold, skipped=faith_sk)
        _score_tile(c3, "Uniqueness Score", latest["uniqueness_score"], threshold, skipped=uniq_sk)

    with tab_det_scores:
        c4, c5, c6, c7 = st.columns(4)
        _score_tile(c4, "Grounding Citation Match", latest.get("grounding_score", 1.0), threshold, skipped=ground_sk)
        _score_tile(c5, "Structural Compliance", latest.get("structural_compliance_score", 1.0), threshold, skipped=struct_sk)
        _score_tile(c6, "Coverage (Article Recall)", latest.get("coverage_score", 1.0), threshold, skipped=cov_sk)
        _score_tile(c7, "Temporal Coherence", latest.get("temporal_coherence_score", 1.0), threshold, skipped=temp_sk)

    st.caption(
        f"Generated at: {latest['generated_at']}  •  "
        f"Lookback: {latest.get('lookback_days', '?')} day(s)  •  "
        f"Runs evaluated: {len(latest.get('runs_evaluated', []))}"
    )

    # Recommendations for the latest report
    recs = latest.get("recommendations") or []
    if recs:
        st.subheader("📝 Recommendations (latest)")
        for rec in recs:
            if isinstance(rec, str) and rec.startswith("⚠️"):
                st.warning(rec)
            elif isinstance(rec, str):
                st.success(rec)

# ---------------------------------------------------------------------------
# Time-series
# ---------------------------------------------------------------------------
if not history_df.empty and len(history_df) >= 1:
    st.divider()
    st.subheader("📈 Score History")

    # Skipped judges are persisted with a 1.0 placeholder + a "skipped"
    # flag in raw_metrics.  Plotting the placeholder would render a
    # quota-exceeded week as a perfect 100% — mask those points to None
    # so the trend line shows an honest gap instead.
    _RAW_KEY_FOR_METRIC = {
        "classifier_score": "categoriser",
        "faithfulness_score": "faithfulness",
        "uniqueness_score": "uniqueness",
        "grounding_score": "grounding",
        "structural_compliance_score": "structural_compliance",
        "coverage_score": "coverage",
        "temporal_coherence_score": "temporal_coherence",
    }

    def _masked_series(df: pd.DataFrame, score_col: str) -> list:
        import json as _json
        raw_key = _RAW_KEY_FOR_METRIC.get(score_col)
        out = []
        for _, row in df.iterrows():
            val = row.get(score_col)
            raw = row.get("raw_metrics") or {}
            if isinstance(raw, str):
                try:
                    raw = _json.loads(raw)
                except Exception:
                    raw = {}
            skipped = (
                isinstance(raw, dict)
                and raw_key is not None
                and bool((raw.get(raw_key) or {}).get("skipped"))
            )
            out.append(None if skipped or pd.isna(val) else float(val))
        return out

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=history_df["generated_at"],
        y=_masked_series(history_df, "classifier_score"),
        mode="lines+markers",
        name="Classifier",
        line=dict(color="#1f77b4", width=3),
    ))
    fig.add_trace(go.Scatter(
        x=history_df["generated_at"],
        y=_masked_series(history_df, "faithfulness_score"),
        mode="lines+markers",
        name="Faithfulness",
        line=dict(color="#2ca02c", width=3),
    ))
    fig.add_trace(go.Scatter(
        x=history_df["generated_at"],
        y=_masked_series(history_df, "uniqueness_score"),
        mode="lines+markers",
        name="Uniqueness",
        line=dict(color="#ff7f0e", width=3),
    ))
    if "grounding_score" in history_df.columns:
        fig.add_trace(go.Scatter(
            x=history_df["generated_at"],
            y=_masked_series(history_df, "grounding_score"),
            mode="lines+markers",
            name="Grounding",
            line=dict(color="#9467bd", width=2),
        ))
    if "structural_compliance_score" in history_df.columns:
        fig.add_trace(go.Scatter(
            x=history_df["generated_at"],
            y=_masked_series(history_df, "structural_compliance_score"),
            mode="lines+markers",
            name="Structural Compliance",
            line=dict(color="#8c564b", width=2),
        ))
    if "coverage_score" in history_df.columns:
        fig.add_trace(go.Scatter(
            x=history_df["generated_at"],
            y=_masked_series(history_df, "coverage_score"),
            mode="lines+markers",
            name="Coverage",
            line=dict(color="#e377c2", width=2),
        ))
    if "temporal_coherence_score" in history_df.columns:
        fig.add_trace(go.Scatter(
            x=history_df["generated_at"],
            y=_masked_series(history_df, "temporal_coherence_score"),
            mode="lines+markers",
            name="Temporal Coherence",
            line=dict(color="#17becf", width=2),
        ))
    fig.add_hline(
        y=threshold,
        line_dash="dash",
        line_color="red",
        annotation_text=f"threshold {threshold:.0%}",
        annotation_position="top left",
    )
    fig.update_yaxes(range=[0, 1], title="Score")
    fig.update_layout(
        height=400,
        margin=dict(l=10, r=10, t=20, b=20),
        hovermode="x unified",
    )
    st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------------------
# Per-theme classifier heatmap
# ---------------------------------------------------------------------------
if not history_df.empty:
    st.divider()
    st.subheader("🧩 Per-Theme Classifier Accuracy")

    # per_theme_classifier is a JSONB dict per row; expand into columns.
    heatmap_rows = []
    for _, row in history_df.iterrows():
        per_theme = row.get("per_theme_classifier") or {}
        if isinstance(per_theme, dict):
            entry = {"generated_at": row["generated_at"]}
            for theme in THEME_ORDER:
                entry[theme] = per_theme.get(theme)
            heatmap_rows.append(entry)

    if heatmap_rows:
        heat_df = pd.DataFrame(heatmap_rows).set_index("generated_at")
        # Only show themes with at least one non-null value
        present = [t for t in THEME_ORDER if t in heat_df.columns and heat_df[t].notna().any()]
        heat_df = heat_df[present]

        if not heat_df.empty:
            fig_h = px.imshow(
                heat_df.T.values,
                x=[t.strftime("%Y-%m-%d %H:%M") if hasattr(t, "strftime") else str(t) for t in heat_df.index],
                y=list(heat_df.columns),
                color_continuous_scale="RdYlGn",
                zmin=0.0,
                zmax=1.0,
                aspect="auto",
                labels=dict(x="Evaluation", y="Theme", color="Score"),
            )
            fig_h.update_layout(height=350, margin=dict(l=10, r=10, t=20, b=20))
            st.plotly_chart(fig_h, width="stretch")
        else:
            st.info("No per-theme classifier data in the current history.")
    else:
        st.info("No per-theme classifier data in the current history.")

# ---------------------------------------------------------------------------
# Raw history table
# ---------------------------------------------------------------------------
if not history_df.empty:
    st.divider()
    st.subheader("📜 Raw History")
    display_cols = [
        "generated_at",
        "lookback_days",
        "threshold",
        "classifier_score",
        "faithfulness_score",
        "uniqueness_score",
    ]
    display_cols = [c for c in display_cols if c in history_df.columns]
    st.dataframe(
        history_df[display_cols].sort_values("generated_at", ascending=False),
        width="stretch",
        hide_index=True,
    )

# ---------------------------------------------------------------------------
# In-App Pipeline Tuner & Keyword Manager (No Backend Editing Required)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("🛠️ In-App Pipeline Tuner & Keyword Manager")
st.caption("Modify taxonomy keywords and tuning parameters directly in the application without editing backend files.")

tab_kw, tab_sum, tab_cls = st.tabs(["🎛️ Theme Keyword Manager", "⚙️ Faithfulness & Summariser Tuner", "🧭 Classification Mode"])

with tab_kw:
    from config.themes import THEMES, THEME_ORDER, add_keywords_to_theme, remove_keyword_from_theme
    from core.classifier import should_suggest_deterministic_mode

    # Suggest switching to deterministic mode if Gate 3 is rarely needed
    if should_suggest_deterministic_mode():
        st.success(
            "💡 **Suggestion:** Recent runs show Gate 3 (LLM classification) catches fewer than 5% of articles. "
            "Consider switching to **Deterministic Mode** in the 🧭 Classification Mode tab to skip LLM classification entirely.",
            icon="🧭",
        )

    selected_theme = st.selectbox("Select Theme to Manage", THEME_ORDER, key="sel_theme_mgr")
    current_keywords = THEMES[selected_theme].get("keywords", {})

    st.markdown(f"#### ➕ Add New Keyword to `{selected_theme}`")
    col_add1, col_add2, col_add3 = st.columns([3, 1, 1])
    with col_add1:
        new_term = st.text_input("Keyword / Phrase", key="txt_new_kw", placeholder="e.g. agentic mesh")
    with col_add2:
        new_weight = st.selectbox("Signal Weight", [1, 2, 3], index=1, format_func=lambda w: f"Weight {w} ({'Generic' if w==1 else 'Strong' if w==2 else 'Specialist'})", key="sel_new_weight")
    with col_add3:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if st.button("➕ Add Keyword", key="btn_add_kw", type="primary"):
            if new_term.strip():
                add_keywords_to_theme(selected_theme, {new_term.strip(): new_weight})
                st.toast(f"Added '{new_term.strip()}' (weight {new_weight}) to {selected_theme}!", icon="✅")
                st.rerun()

    st.markdown(f"#### 📋 Current Keywords for `{selected_theme}` ({len(current_keywords)} active)")
    kw_items = sorted(current_keywords.items(), key=lambda kv: (-kv[1], kv[0]))
    cols_per_row = 3
    for i in range(0, len(kw_items), cols_per_row):
        c_group = st.columns(cols_per_row)
        for j, (kw_name, kw_wt) in enumerate(kw_items[i:i+cols_per_row]):
            with c_group[j]:
                col_lbl, col_btn = st.columns([4, 1])
                col_lbl.markdown(f"`{kw_name}` *(wt: {kw_wt})*")
                del_key = f"del_confirm_{selected_theme}_{kw_name}"
                if del_key not in st.session_state:
                    st.session_state[del_key] = False

                if not st.session_state[del_key]:
                    if col_btn.button("❌", key=f"del_{selected_theme}_{kw_name}", help=f"Remove '{kw_name}'"):
                        st.session_state[del_key] = True
                        st.rerun()
                else:
                    if col_btn.button("Confirm Delete?", key=f"confirm_del_{selected_theme}_{kw_name}", type="primary"):
                        remove_keyword_from_theme(selected_theme, kw_name)
                        st.session_state[del_key] = False
                        st.toast(f"Removed '{kw_name}' from {selected_theme}!", icon="🗑️")
                        st.rerun()

with tab_sum:
    from config.settings import get_summariser_settings, update_summariser_settings
    cur_s = get_summariser_settings()

    st.markdown("#### ⚙️ Summariser, Faithfulness & Coverage Tuning")
    st.info("💡 **Coverage Tip**: If **Coverage score** is low (<70%), increase the **Max Predict Token Budget** slider to 2,000–2,500 tokens so the summariser does not cut off or omit source articles.")

    t_strict = st.toggle(
        "🛡️ Enable Strict Anti-Hallucination Grounding Mode",
        value=cur_s.get("strict_faithfulness_mode", False),
        help="Appends strict source-grounding rules to the summariser system prompt."
    )
    s_temp = st.slider(
        "Summariser Temperature",
        min_value=0.0, max_value=1.0, value=float(cur_s.get("temperature", 0.3)), step=0.05,
        help="Lower temperature (e.g. 0.1) produces strictly deterministic outputs and reduces fabrication."
    )
    s_tokens = st.slider(
        "Max Predict Token Budget (Fixes Low Coverage)",
        min_value=500, max_value=3000, value=int(cur_s.get("max_tokens", 1500)), step=100,
        help="Response token budget for output summaries. Increase to 2,000–2,500 tokens if Coverage score is low (<70%)."
    )

    if st.button("💾 Save Tuner Settings", key="btn_save_sum_settings", type="primary"):
        update_summariser_settings(s_temp, s_tokens, t_strict)
        st.toast("Saved summariser, faithfulness & coverage settings!", icon="✅")
        st.success("Updated active summariser tuning parameters successfully!")
        st.rerun()

with tab_cls:
    from config.settings import get_classification_settings, update_classification_settings
    from core.classifier import get_latest_gate_stats, should_suggest_deterministic_mode

    cls_settings = get_classification_settings()
    current_mode = cls_settings.get("classification_mode", "hybrid")

    st.markdown("#### 🧭 Classification Mode")
    st.caption("Control whether the 4-pass classification pipeline uses LLM classification (Gate 3).")

    new_mode = st.radio(
        "Classification Mode",
        options=["hybrid", "deterministic"],
        index=0 if current_mode == "hybrid" else 1,
        format_func=lambda m: f"{'🔀 Hybrid' if m == 'hybrid' else '🧭 Deterministic'} — "
                              f"{'4-pass waterfall (keyword → TF-IDF → LLM → heuristic)' if m == 'hybrid' else '3-pass deterministic (keyword → TF-IDF → heuristic, no LLM)'}",
        help="Hybrid: Uses LLM (Gate 3) for articles that miss keyword/TF-IDF. Deterministic: Skips LLM entirely, uses heuristic fallback only.",
    )

    if new_mode != current_mode:
        if st.button(f"Switch to **{new_mode}** mode", key="btn_switch_cls_mode", type="primary"):
            update_classification_settings(classification_mode=new_mode)
            st.toast(f"Classification mode changed to **{new_mode}**!", icon="✅")
            st.rerun()
    else:
        st.info(f"Current mode: **{current_mode}**")

    # Gate stats from the most recent run
    st.divider()
    st.markdown("#### 📊 Latest Gate Stats")
    gate_stats = get_latest_gate_stats()
    if gate_stats.get("total", 0) > 0:
        total = gate_stats["total"]
        g1 = gate_stats.get("gate_1_keyword", 0)
        g2 = gate_stats.get("gate_2_tfidf", 0)
        g3 = gate_stats.get("gate_3_llm", 0)
        g4 = gate_stats.get("gate_4_heuristic", 0)
        col_g1, col_g2, col_g3, col_g4 = st.columns(4)
        col_g1.metric("Gate 1 (Keywords)", f"{g1} ({g1/total*100:.0f}%)")
        col_g2.metric("Gate 2 (TF-IDF)", f"{g2} ({g2/total*100:.0f}%)")
        col_g3.metric("Gate 3 (LLM)", f"{g3} ({g3/total*100:.0f}%)")
        col_g4.metric("Gate 4 (Heuristic)", f"{g4} ({g4/total*100:.0f}%)")
    else:
        st.caption("No classification run recorded yet. Run a data refresh to see gate stats.")

    # Gate stats history table
    history = cls_settings.get("gate_stats_history", [])
    if history:
        st.divider()
        st.markdown("#### 📈 Gate Stats History (last 20 runs)")
        import pandas as pd
        hist_df = pd.DataFrame(history)
        display_cols = [c for c in ["timestamp", "mode", "total", "gate_1_keyword", "gate_2_tfidf", "gate_3_llm", "gate_4_heuristic", "gate3_rate"] if c in hist_df.columns]
        if display_cols:
            st.dataframe(hist_df[display_cols].sort_values("timestamp", ascending=False), use_container_width=True, hide_index=True)

