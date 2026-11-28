"""
AI Pulse - Eval Sets Page
Browse every git-tracked evaluation question set (eval_sets/*.json) with
pagination, ground-truth answers, and the latest recorded run scores.
Adding a new eval set JSON to eval_sets/ makes it appear here automatically.
"""

import streamlit as st

from core.design_system import apply_design_system
from core.shared_sidebar import render_sidebar_nav
from core.bg_refresher import check_and_show_bg_status
from core.eval_sets import (
    DEFAULT_PAGE_SIZE,
    discover_eval_sets,
    get_latest_results,
    paginate,
    result_score,
    summary_mean,
)

# Page configuration
st.set_page_config(
    page_title="Eval Sets - AI Pulse",
    page_icon="⚡",
    layout="wide"
)

apply_design_system()


def _results_by_id(results: dict) -> dict:
    """Index a run snapshot's per_question list by question id (or text)."""
    out = {}
    for r in (results or {}).get("per_question", []) or []:
        key = r.get("id") or r.get("question")
        if key:
            out[key] = r
    return out


def _render_question(idx: int, q: dict, result: dict) -> None:
    """One question card: question, ground truth, and latest score if any."""
    with st.container(border=True):
        head_col, score_col = st.columns([5, 1])
        with head_col:
            st.markdown(f"**Q{idx}. {q.get('question', '')}**")
        with score_col:
            score = result_score(result)
            if score is not None:
                pct = score * 100
                icon = "🟢" if pct >= 99 else ("🟡" if pct >= 50 else "🔴")
                st.markdown(f"{icon} **{pct:.0f}%**")
            elif result:
                st.markdown("⚪ skipped")

        keywords = q.get("expected_keywords") or []
        if keywords:
            st.markdown(
                "**Expected keywords:** " + " ".join(f"`{k}`" for k in keywords)
            )
        if q.get("answer"):
            st.markdown(f"**Answer:** {q['answer']}")
        if q.get("target_title"):
            st.caption(f"🎯 Ground-truth article: {q['target_title']}")
        if q.get("notes"):
            st.caption(f"📝 {q['notes']}")
        if result and result.get("recall") is not None:
            st.caption(
                f"Latest run: {result.get('hits', 0)}/{result.get('expected', 0)} "
                f"keywords found in top-{q.get('k', 10)} retrieved chunks"
            )
        elif result and result.get("score") is not None:
            claims = result.get("unsupported_claims") or []
            claims_note = f" · {len(claims)} unsupported claims" if claims else ""
            st.caption(
                f"Latest run: faithfulness {result['score']:.2f} · "
                f"{result.get('context_chars', 0):,} context chars · "
                f"{result.get('latency_ms', 0):.0f}ms{claims_note}"
            )
            if result.get("legacy_score") is not None:
                st.caption(
                    f"Legacy arm: {result['legacy_score']:.2f} · "
                    f"{result.get('legacy_context_chars', 0):,} chars"
                )


def main() -> None:
    """Main eval sets page."""
    check_and_show_bg_status()
    render_sidebar_nav()

    st.title("🧪 Eval Sets")
    st.markdown("### Evaluation question sets & latest recorded scores")
    st.divider()

    sets = discover_eval_sets()
    if not sets:
        st.info(
            "No eval sets found. Add a JSON file to `eval_sets/` "
            "(see `core/eval_sets.py` for the schema) and it will appear here."
        )
        return

    names = [s["name"] for s in sets]
    sel_name = st.selectbox("Select eval set:", names, key="sel_eval_set")
    eval_set = next(s for s in sets if s["name"] == sel_name)
    set_id = eval_set.get("id", sel_name)

    st.markdown(f"#### {eval_set['name']}")
    if eval_set.get("description"):
        st.caption(eval_set["description"])

    questions = eval_set.get("questions", [])
    results = get_latest_results(eval_set) or {}
    results_map = _results_by_id(results)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Questions", len(questions))
    m2.metric("Metric", eval_set.get("metric", "—"))
    m3.metric("Created", eval_set.get("created", "—"))
    mean = summary_mean(results)
    m4.metric(
        "Latest mean score",
        f"{mean:.3f}" if mean is not None else "no run yet",
    )
    if results.get("ran_at"):
        st.caption(
            f"🕒 Latest run: {results['ran_at']} · "
            f"{results.get('n_scored', 0)}/{results.get('n_questions', 0)} questions scored"
        )
    if "gate_passed" in results:
        icon = "✅" if results["gate_passed"] else "❌"
        st.caption(f"{icon} Gate verdict: {'PASSED' if results['gate_passed'] else 'NOT PASSED'}")
    if eval_set.get("runner"):
        st.caption(f"▶️ Run this eval: `{eval_set['runner']}`")

    st.divider()

    # --- Pagination (20 questions per page) ---
    page_key = f"eval_page_{set_id}"
    page = st.session_state.get(page_key, 1)
    page_questions, page, total_pages = paginate(questions, page, DEFAULT_PAGE_SIZE)
    st.session_state[page_key] = page

    nav_prev, nav_info, nav_next = st.columns([1, 3, 1])
    with nav_prev:
        if st.button("⬅️ Previous", key=f"eval_prev_{set_id}", disabled=(page <= 1), width='stretch'):
            st.session_state[page_key] = page - 1
            st.rerun()
    with nav_info:
        start = (page - 1) * DEFAULT_PAGE_SIZE + 1
        end = (page - 1) * DEFAULT_PAGE_SIZE + len(page_questions)
        st.markdown(
            f"<div style='text-align:center; padding-top:6px;'>"
            f"Page <b>{page}</b> of <b>{total_pages}</b> · questions {start}–{end} of {len(questions)}"
            f"</div>",
            unsafe_allow_html=True,
        )
    with nav_next:
        if st.button("Next ➡️", key=f"eval_next_{set_id}", disabled=(page >= total_pages), width='stretch'):
            st.session_state[page_key] = page + 1
            st.rerun()

    for i, q in enumerate(page_questions):
        idx = (page - 1) * DEFAULT_PAGE_SIZE + i + 1
        result = results_map.get(q.get("id") or q.get("question"))
        _render_question(idx, q, result)


if __name__ == "__main__":
    main()