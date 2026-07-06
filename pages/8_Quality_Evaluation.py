"""
AI Pulse - Quality Evaluation Page
On-demand and weekly-view of classifier, faithfulness, and uniqueness scores
produced by three concurrent LLM judge agents in core/evaluator.py.
"""

from __future__ import annotations

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime

from config.settings import QUALITY_THRESHOLD
from config.themes import THEME_ORDER
from core.supabase_client import get_supabase_manager
from core.shared_sidebar import render_sidebar_nav
from core.bg_refresher import check_and_show_bg_status

st.set_page_config(page_title="Quality Evaluation - AI Pulse", page_icon="🔬", layout="wide")

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
    progress = st.progress(0.0, text="Starting judges…")
    status = st.empty()

    def _tick(pct: float, msg: str) -> None:
        progress.progress(pct, text=msg)
        status.info(msg)

    try:
        _tick(0.10, "Loading recent runs from Supabase…")
        from core.evaluator import run_weekly_evaluation

        # Run the evaluation; the three judges inside run in their own thread
        # pool so the Streamlit thread stays responsive.  We just show
        # coarse progress markers.
        _tick(0.25, "Running 3 LLM judges in parallel (this takes a few minutes)…")
        report = run_weekly_evaluation(
            supabase=supabase,
            lookback_days=lookback_days,
            threshold=threshold,
        )
        _tick(1.0, "Done.  Reloading history…")
        # Invalidate the history cache so the new row appears immediately.
        _cached_history.clear()
        st.success(
            f"✅ Evaluation complete — classifier {report.classifier_score:.0%}, "
            f"faithfulness {report.faithfulness_score:.0%}, "
            f"uniqueness {report.uniqueness_score:.0%}."
        )
        # Show recommendations inline.
        if report.recommendations:
            for rec in report.recommendations:
                if rec.startswith("⚠️"):
                    st.warning(rec)
                else:
                    st.success(rec)
    except Exception as exc:  # noqa: BLE001
        progress.empty()
        status.empty()
        st.error(f"❌ Evaluation failed: {exc}")

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

    c1, c2, c3 = st.columns(3)
    _score_tile(c1, "Classifier", latest["classifier_score"], threshold)
    _score_tile(c2, "Faithfulness", latest["faithfulness_score"], threshold)
    _score_tile(c3, "Uniqueness", latest["uniqueness_score"], threshold)

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
