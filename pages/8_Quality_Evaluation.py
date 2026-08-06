"""
AI Pulse - Quality Evaluation Page
On-demand and weekly-view of classifier, faithfulness, and uniqueness scores
produced by three concurrent LLM judge agents in core/evaluator.py.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from queue import Empty as QueueEmpty, Queue

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

from config.settings import QUALITY_THRESHOLD
from config.themes import THEME_ORDER
from core.supabase_client import get_supabase_manager
from core.shared_sidebar import render_sidebar_nav
from core.bg_refresher import check_and_show_bg_status
from core.evaluator import (
    consume_judge_events,
    reset_judge_events,
)

st.set_page_config(page_title="Quality Evaluation - AI Pulse", page_icon="🔬", layout="wide")


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
    "Reference-free quality scoring of the AI Pulse pipeline.  Three LLM judge "
    "agents run in parallel on the recent runs stored in Supabase: a "
    "**Categoriser** judge (does the assigned theme match a fresh classification?), "
    "a **Faithfulness** judge (are the summary claims supported by the source "
    "articles?), and a **Uniqueness** judge (do summaries overlap across themes "
    "and across runs?)."
)

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
# Sidebar controls
# ---------------------------------------------------------------------------
with st.sidebar:
    st.divider()
    st.subheader("🎚️ Evaluation Settings")
    threshold = st.slider(
        "Threshold",
        min_value=0.50,
        max_value=0.99,
        value=QUALITY_THRESHOLD,
        step=0.01,
        format="%.2f",
        help="Scores below this line trigger a recommendation.  The value used "
        "is stored on the resulting evaluation row so historical reports "
        "remain comparable.",
    )
    lookback_days = st.radio(
        "Lookback (days)",
        options=[1, 7, 14, 30],
        index=1,
        horizontal=True,
        help="How far back to look for trend_runs to evaluate.",
    )

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

# ---------------------------------------------------------------------------
# Run-now button
# ---------------------------------------------------------------------------
st.subheader("▶ Run Evaluation")
st.caption(
    "On-demand evaluation.  Three LLM judges run concurrently and the result "
    "is written to the `quality_evaluations` Supabase table."
)
run_now = st.button(
    "Run evaluation now",
    type="primary",
    width="stretch",
    key="run_quality_eval_btn",
)

if run_now:
    # ------------------------------------------------------------------
    # Live progress panel for the running evaluation
    # ------------------------------------------------------------------
    # The three judges run in their own ThreadPoolExecutor inside
    # ``run_weekly_evaluation``.  We start that call in a daemon thread so
    # the Streamlit main thread can keep rendering.  A short poll loop
    # below drains ``consume_judge_events()`` every ~500 ms and updates
    # the per-judge sub-status, the running totals, and the live events
    # dataframe.  This is the only place where the page is allowed to
    # block — every other section is gated by ``st.session_state`` so
    # subsequent reruns pick up where we left off.
    #
    # Cross-thread result handoff uses a ``queue.Queue`` and a
    # ``threading.Event`` rather than direct ``st.session_state`` writes.
    # Streamlit's session_state is not designed to be mutated by a
    # background thread — the main thread's snapshot can lag behind the
    # worker's writes, leaving us with ``report=None`` *and* ``error=None``
    # simultaneously, which previously crashed on ``report.classifier_score``.
    reset_judge_events()
    progress_state = st.session_state.setdefault("eval_progress", {
        "running": False,
        "error": None,
        "report": None,
        "events": [],
    })
    progress_state.update({"running": True, "error": None, "report": None, "events": []})

    # Thread-safe handoff: worker puts (kind, payload) tuples here;
    # main thread drains at the end.
    result_queue: Queue = Queue(maxsize=4)
    completion_event = threading.Event()

    from core.evaluator import run_weekly_evaluation

    def _run_eval() -> None:
        try:
            report = run_weekly_evaluation(
                supabase=supabase,
                lookback_days=lookback_days,
                threshold=threshold,
            )
            if report is None:
                result_queue.put(("error", "run_weekly_evaluation returned None"))
            else:
                result_queue.put(("ok", report))
        except Exception as exc:  # noqa: BLE001
            result_queue.put(("error", str(exc)))
        finally:
            completion_event.set()

    eval_thread = threading.Thread(target=_run_eval, name="quality-eval-page", daemon=True)
    eval_thread.start()

    panel = st.container()
    with panel:
        st.markdown("#### 🔍 Live Judge Progress")
        col_cat, col_faith, col_uniq = st.columns(3)
        cat_box = col_cat.empty()
        faith_box = col_faith.empty()
        uniq_box = col_uniq.empty()
        table_placeholder = st.empty()
        summary_placeholder = st.empty()
        st.caption("Auto-refreshing every ~500 ms while judges run.")

    # Poll loop.  Streamlit lets a script run for a few minutes here as
    # long as it yields via time.sleep.  We cap the loop at 10 minutes
    # so a hung LLM can't pin the page forever.
    loop_deadline = time.monotonic() + 600
    last_render = 0.0
    while progress_state["running"] and time.monotonic() < loop_deadline:
        events = consume_judge_events()
        if events:
            progress_state["events"].extend(events)
            # Keep only the most recent 200 in session to bound memory.
            progress_state["events"] = progress_state["events"][-200:]

        # If the worker has signalled completion we can stop polling
        # early.  We still keep the running flag as a defensive check
        # in case the queue/Event race with the dict update.
        if completion_event.is_set():
            break

        now = time.monotonic()
        if now - last_render >= 0.4:
            last_render = now
            evs = progress_state["events"]
            # Per-judge counts
            cat_events = [e for e in evs if e["judge"] == "categoriser"]
            faith_events = [e for e in evs if e["judge"] == "faithfulness"]
            uniq_events = [e for e in evs if e["judge"] == "uniqueness"]

            def _render_judge(box, name: str, items: list, score_attr: str = "score") -> None:
                done = len(items)
                ok = sum(1 for i in items if i.get("parse_ok"))
                scores = [i["score"] for i in items if i.get("score") is not None]
                mean_s = sum(scores) / len(scores) if scores else 0.0
                lat = [i["latency_ms"] for i in items if i.get("latency_ms")]
                mean_lat = sum(lat) / len(lat) if lat else 0
                with box.container():
                    st.metric(name, f"{done} done", delta=f"ok {ok}/{done}")
                    st.progress(min(1.0, done / max(1, 20)))
                    st.caption(
                        f"mean score {mean_s:.2f} • mean latency {int(mean_lat)} ms"
                    )

            _render_judge(cat_box, "Categoriser", cat_events)
            _render_judge(faith_box, "Faithfulness", faith_events)
            _render_judge(uniq_box, "Uniqueness", uniq_events)

            recent = evs[-50:]
            if recent:
                df = pd.DataFrame(recent)
                df["ts"] = pd.to_datetime(df["ts"], unit="s").dt.strftime("%H:%M:%S")
                df = df[["ts", "judge", "run_id", "item_id", "latency_ms", "parse_ok", "score"]]
                table_placeholder.dataframe(df, hide_index=True, width="stretch")
        time.sleep(0.25)

    # Final drain of any events that arrived between the last render and
    # the worker finishing.
    final_events = consume_judge_events()
    if final_events:
        progress_state["events"].extend(final_events)
        progress_state["events"] = progress_state["events"][-200:]

    # Wait for the worker to actually finish (bounded).  Once
    # completion_event is set, the result_queue holds the worker's
    # verdict and the worker's writes are visible to this thread.
    completion_event.wait(timeout=10.0)
    eval_thread.join(timeout=2.0)

    # Drain the thread-safe result queue and persist into progress_state
    # for downstream rendering.  ``progress_state["report"]`` /
    # ``progress_state["error"]`` are now read-after-write from a single
    # thread (this one) so the None race is gone.
    worker_error = None  # type: ignore[var-annotated]
    worker_report = None
    try:
        kind, payload = result_queue.get_nowait()
    except QueueEmpty:
        kind, payload = ("error", "Evaluation timed out before the worker could report a result.")
    if kind == "ok":
        worker_report = payload
        progress_state["report"] = payload
        progress_state["error"] = None
    else:
        worker_error = payload
        progress_state["error"] = payload
        progress_state["report"] = None
    progress_state["running"] = False

    if worker_error:
        st.error(f"❌ Evaluation failed: {worker_error}")
    elif worker_report is None:
        st.error("❌ Evaluation failed: No evaluation report was generated.")
    else:
        report = worker_report
        with summary_placeholder.container():
            evs = progress_state["events"]
            llm_calls = sum(1 for e in evs if e["judge"] != "uniqueness" or e.get("latency_ms", 0) > 0)
            parse_fail = sum(1 for e in evs if not e.get("parse_ok"))
            latencies = [e["latency_ms"] for e in evs if e.get("latency_ms", 0) > 0]
            mean_lat = int(sum(latencies) / len(latencies)) if latencies else 0
            st.success(
                f"✅ Evaluation complete — classifier {report.classifier_score:.0%}, "
                f"faithfulness {report.faithfulness_score:.0%}, "
                f"uniqueness {report.uniqueness_score:.0%}."
            )
            st.caption(
                f"Judge events: {len(evs)} • LLM calls: ~{llm_calls} • "
                f"parse failures: {parse_fail} • mean latency: {mean_lat} ms"
            )

            # Render Classification Waterfall Gate Breakdown
            raw_m = getattr(report, "raw_metrics", {}) or {}
            gates = raw_m.get("classifier_gates")
            if not gates:
                from core.classifier import get_latest_gate_stats
                gates = get_latest_gate_stats()

            if isinstance(gates, dict) and gates.get("total", 0) > 0:
                tot = gates["total"]
                p1 = gates.get("gate_1_keyword", 0)
                p2 = gates.get("gate_2_tfidf", 0)
                p3 = gates.get("gate_3_ollama", 0)
                p4 = gates.get("gate_4_heuristic", 0)

                st.markdown("##### 🚪 Classification Pipeline Gate Breakdown")
                gc1, gc2, gc3, gc4 = st.columns(4)
                gc1.metric("Pass 1: Keyword", f"{p1}/{tot}", f"{p1/tot:.0%}" if tot else "0%")
                gc2.metric("Pass 2: TF-IDF", f"{p2}/{tot}", f"{p2/tot:.0%}" if tot else "0%")
                gc3.metric("Pass 3: Ollama LLM", f"{p3}/{tot}", f"{p3/tot:.0%}" if tot else "0%")
                gc4.metric("Pass 4: Heuristic", f"{p4}/{tot}", f"{p4/tot:.0%}" if tot else "0%")
        if report.recommendations:
            for rec in report.recommendations:
                if rec.startswith("⚠️"):
                    st.warning(rec)
                else:
                    st.success(rec)

        # Keyword + watchlist suggestions — generated automatically when
        # the evaluator finds weak themes.  Render below recommendations
        # so the user can copy the suggestions into config/themes.py or
        # watch.md without leaving the page.
        kw = getattr(report, "keyword_suggestions", None)
        if isinstance(kw, dict) and (
            kw.get("theme_suggestions") or kw.get("watchlist_suggestions")
        ):
            st.markdown("#### 🧠 Suggested theme keywords")
            theme_map = kw.get("theme_suggestions") or {}
            for theme, items in theme_map.items():
                if not items:
                    continue
                with st.expander(f"📁 {theme} — {len(items)} new keyword(s)", expanded=False):
                    rows_text = "| term | weight | reason |\n|---|---|---|\n"
                    for it in items:
                        term = (it.get("term") or "").replace("|", "\\|")
                        weight = it.get("weight", "")
                        reason = (it.get("reason") or "").replace("|", "\\|").replace("\n", " ")
                        rows_text += f"| {term} | {weight} | {reason} |\n"
                    st.markdown(rows_text)
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

    def _score_tile(col, label: str, score: float, threshold_val: float) -> None:
        with col:
            pct = max(0.0, min(1.0, float(score)))
            ok = pct >= threshold_val
            color = "#1f9d55" if ok else "#c0392b"
            st.metric(label, f"{pct:.0%}", delta=f"threshold {threshold_val:.0%}")
            st.progress(pct)
            st.markdown(
                f"<div style='height:6px;border-radius:3px;background:{color}'></div>",
                unsafe_allow_html=True,
            )

    c1, c2, c3, c4 = st.columns(4)
    _score_tile(c1, "Classifier", latest["classifier_score"], threshold)
    _score_tile(c2, "Faithfulness", latest["faithfulness_score"], threshold)
    _score_tile(c3, "Uniqueness", latest["uniqueness_score"], threshold)
    _score_tile(c4, "Grounding", latest.get("grounding_score", 1.0), threshold)

    c5, c6, c7, _ = st.columns(4)
    _score_tile(c5, "Structural Compliance", latest.get("structural_compliance_score", 1.0), threshold)
    _score_tile(c6, "Coverage", latest.get("coverage_score", 1.0), threshold)
    _score_tile(c7, "Temporal Coherence", latest.get("temporal_coherence_score", 1.0), threshold)

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

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=history_df["generated_at"],
        y=history_df["classifier_score"],
        mode="lines+markers",
        name="Classifier",
        line=dict(color="#1f77b4", width=3),
    ))
    fig.add_trace(go.Scatter(
        x=history_df["generated_at"],
        y=history_df["faithfulness_score"],
        mode="lines+markers",
        name="Faithfulness",
        line=dict(color="#2ca02c", width=3),
    ))
    fig.add_trace(go.Scatter(
        x=history_df["generated_at"],
        y=history_df["uniqueness_score"],
        mode="lines+markers",
        name="Uniqueness",
        line=dict(color="#ff7f0e", width=3),
    ))
    if "grounding_score" in history_df.columns:
        fig.add_trace(go.Scatter(
            x=history_df["generated_at"],
            y=history_df["grounding_score"],
            mode="lines+markers",
            name="Grounding",
            line=dict(color="#9467bd", width=2),
        ))
    if "structural_compliance_score" in history_df.columns:
        fig.add_trace(go.Scatter(
            x=history_df["generated_at"],
            y=history_df["structural_compliance_score"],
            mode="lines+markers",
            name="Structural Compliance",
            line=dict(color="#8c564b", width=2),
        ))
    if "coverage_score" in history_df.columns:
        fig.add_trace(go.Scatter(
            x=history_df["generated_at"],
            y=history_df["coverage_score"],
            mode="lines+markers",
            name="Coverage",
            line=dict(color="#e377c2", width=2),
        ))
    if "temporal_coherence_score" in history_df.columns:
        fig.add_trace(go.Scatter(
            x=history_df["generated_at"],
            y=history_df["temporal_coherence_score"],
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
