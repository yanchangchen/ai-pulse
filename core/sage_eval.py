"""Phase 6.2 gate: macro-faithfulness eval of tier-aware Sage assembly.

Two arms answer the same multi-month questions through the real gateway:
``legacy`` (sage_tier3_enabled=False — byte-identical pre-6.2 assembly) and
``tier3`` (live defaults).  The Phase 2 faithfulness judge scores each answer
against its OWN arm's assembled context, so the comparison measures whether
Tier-3 rollups preserve groundedness while extending macro reach.

Gate (all must hold):
  * faithfulness: tier3 mean score >= legacy mean score - FAITHFULNESS_EPS
    (judge-noise band, cf. the 4.4 "within noise" precedent);
  * efficiency: tier3 mean context chars PER COVERED MONTH <= legacy's
    (1 - EFFICIENCY_MIN_REDUCTION).  Absolute chars are NOT the metric:
    both arms fill the same 40k budget greedily, and Tier-3 extends reach
    to months the legacy limit=300 Tier-2 window never reaches — the
    Pillar B claim is same-or-better reach for far fewer chars per month;
  * coverage: tier3 month_coverage >= COVERAGE_MIN — guards against the
    judge rewarding high-scoring "I have no data" refusals;
  * both arms score >= MIN_SCORED_PER_ARM questions (infra failures are
    excluded rows, never zeros).

The judge's context truncation is raised to JUDGE_CONTEXT_CHARS for BOTH
arms (core.evaluator.EVAL_SAGE_CONTEXT_CHARS defaults to 8000 — live Sage
contexts are ~30-40k chars, chronological ascending, so the default would
show the judge only the oldest prefix and rig the comparison).

Nothing calls this at runtime — run it manually:

    python -m core.sage_eval --gate          # side-by-side gate
    python -m core.sage_eval --arm tier3     # single arm

The labelled set lives in ``eval_sets/sage_macro_faithfulness.json``
(git-tracked, discoverable by the Eval Sets page).  Verdict + per-question
rows are snapshotted to app_settings ``sage_eval_latest``.  Eval rows are
NOT written to sage_evaluations — the rolling chat-time baseline stays
clean.  Cost: 8 questions x 2 arms x (1 chat + 1 judge) = 32 LLM calls.
"""
import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import patch

logger = logging.getLogger(__name__)

SAGE_SET_ID = "sage-macro-faithfulness"

FAITHFULNESS_EPS = 0.02
EFFICIENCY_MIN_REDUCTION = 0.30
COVERAGE_MIN = 0.75
MIN_SCORED_PER_ARM = 6
JUDGE_CONTEXT_CHARS = 60_000


def load_questions(path: Optional[Path] = None) -> List[Dict]:
    if path is not None:
        if not path.exists():
            logger.warning("Sage eval questions not found at %s", path)
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("questions", [])

    from core.eval_sets import load_eval_set

    eval_set = load_eval_set(SAGE_SET_ID)
    if not eval_set:
        logger.warning("Sage eval set '%s' not found in eval_sets/", SAGE_SET_ID)
        return []
    return eval_set.get("questions", [])


def _month_span(start_ym: str, end_ym: str) -> List[str]:
    """Inclusive 'YYYY-MM' enumeration between two months."""
    try:
        y, m = int(start_ym[:4]), int(start_ym[5:7])
        ye, me = int(end_ym[:4]), int(end_ym[5:7])
    except (ValueError, TypeError):
        return []
    out: List[str] = []
    while (y, m) <= (ye, me) and len(out) < 240:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _covered_months(ctx: Dict) -> List[str]:
    """Months the assembled context actually covers: Tier-3 render months
    plus the Tier-2 date-range span (the window is continuous)."""
    months = set(ctx.get("tier3_months") or [])
    dr = str(ctx.get("date_range") or "")
    if "→" in dr:
        a, b = dr.split("→", 1)
        months.update(_month_span(a.strip()[:7], b.strip()[:7]))
    elif dr:
        months.add(dr[:7])
    return sorted(months)


def _summarize(per_question: List[Dict], arm: str) -> Dict:
    scored = [r for r in per_question if r.get("score") is not None]
    mean_score = sum(r["score"] for r in scored) / len(scored) if scored else 0.0
    cpm = [
        r["context_chars"] / len(r["covered_months"])
        for r in scored if r.get("covered_months") and r.get("context_chars")
    ]
    mean_cpm = sum(cpm) / len(cpm) if cpm else 0.0
    hits = sum(r.get("expected_month_hits", 0) for r in per_question)
    expected = sum(r.get("expected_months", 0) for r in per_question)
    latencies = [r["latency_ms"] for r in per_question if r.get("latency_ms") is not None]
    return {
        "ran": True,
        "arm": arm,
        "n_questions": len(per_question),
        "n_scored": len(scored),
        "mean_score": mean_score,
        "mean_chars_per_month": round(mean_cpm, 1),
        "month_coverage": (hits / expected) if expected else 0.0,
        "latency_mean_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        "per_question": per_question,
    }


def run_sage_eval(path: Optional[Path] = None, quiet: bool = False, arm: str = "tier3") -> Dict:
    """Run one arm of the macro-faithfulness eval; returns a summary dict.

    ``arm``: "legacy" (kill-switch override) or "tier3" (live defaults).
    Full Archive bounds and live max_chars/limit — matches the UI call site.
    """
    from core.evaluator import GatewayJudgeLLM, _gateway_has_providers, judge_sage_answer
    from core.sage_agent import build_wiki_context, chat_with_sage
    from core.sage_pipeline import chat_with_sage_deep
    from core.supabase_client import get_supabase_manager

    questions = load_questions(path)
    if not questions:
        return {"ran": False, "reason": "no_questions"}
    manager = get_supabase_manager()
    if not manager or not manager.is_available():
        return {"ran": False, "reason": "supabase_unavailable"}
    if not _gateway_has_providers():
        return {"ran": False, "reason": "no_gateway_providers"}

    judge = GatewayJudgeLLM(label="sage_macro_eval")
    per_question: List[Dict] = []

    for q in questions:
        question = q.get("question", "")
        expected = q.get("expected_months", [])
        row: Dict = {
            "id": q.get("id"),
            "question": question,
            "score": None,
            "unsupported_claims": None,
            "failure": None,
            "context_chars": 0,
            "covered_months": [],
            "tier3_block_count": 0,
            "expected_month_hits": 0,
            "expected_months": len(expected),
            "latency_ms": None,
        }
        ctx: Dict = {}
        t0 = time.perf_counter()
        try:
            # Raise the judge's context window for BOTH arms symmetrically.
            with patch("core.evaluator.EVAL_SAGE_CONTEXT_CHARS", JUDGE_CONTEXT_CHARS):
                if arm == "legacy":
                    from config.settings import _SAGE_DEFAULTS
                    with patch(
                        "config.settings.get_sage_settings",
                        return_value={**_SAGE_DEFAULTS, "sage_tier3_enabled": False},
                    ):
                        ctx = build_wiki_context(manager, question, history={})
                        result = chat_with_sage([{"role": "user", "content": question}], ctx)
                elif arm == "deep":
                    ctx = build_wiki_context(manager, question, history={})
                    result = chat_with_sage_deep(
                        [{"role": "user", "content": question}], ctx, supabase=manager,
                    )
                else:
                    ctx = build_wiki_context(manager, question, history={})
                    result = chat_with_sage([{"role": "user", "content": question}], ctx)

                # Deep mode rebuilds the evidence section from sub-queries —
                # judge against the context the answer actually saw.
                judge_context = result.get("context") or ctx.get("context", "")
                row["context_chars"] = len(judge_context)
                row["covered_months"] = _covered_months(ctx)
                row["tier3_block_count"] = ctx.get("tier3_block_count", 0)
                row["expected_month_hits"] = sum(
                    1 for m in expected if m in row["covered_months"]
                )
                row["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
                pipe = result.get("pipeline") or {}
                if pipe.get("mode") == "deep":
                    row["sub_queries"] = len(pipe.get("sub_queries") or [])
                    row["revised"] = pipe.get("revised")
                    row["factcheck_ok"] = pipe.get("factcheck_ok")

                prov = result.get("provenance") or {}
                answer = result.get("response", "")
                if prov.get("method") != "llm":
                    row["failure"] = "chat_failed"
                else:
                    score, claims, failure = judge_sage_answer(
                        judge, question, answer, judge_context
                    )
                    row["score"] = score
                    row["unsupported_claims"] = claims
                    row["failure"] = failure
        except Exception as e:
            row["failure"] = f"error: {str(e)[:200]}"
        per_question.append(row)

    summary = _summarize(per_question, arm)

    if not quiet:
        print(f"Sage macro faithfulness [{arm}]: mean = {summary['mean_score']:.3f} "
              f"({summary['n_scored']}/{summary['n_questions']} scored), "
              f"chars/month {summary['mean_chars_per_month']}, "
              f"coverage {summary['month_coverage']:.2f}")
        for r in per_question:
            s = f"{r['score']:.2f}" if r.get("score") is not None else "skip"
            print(f"  {s}  {r['question'][:70]}")

    try:
        manager.upsert_app_setting("sage_eval_latest", {
            "set_id": SAGE_SET_ID,
            "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "arm": arm,
            "mean_score": summary["mean_score"],
            "n_scored": summary["n_scored"],
            "n_questions": summary["n_questions"],
            "mean_chars_per_month": summary["mean_chars_per_month"],
            "month_coverage": summary["month_coverage"],
            "latency_mean_ms": summary["latency_mean_ms"],
            "per_question": per_question,
        })
    except Exception as e:
        logger.debug("Failed to snapshot sage eval to app_settings: %s", e)

    logger.info("Sage eval [%s] mean_score=%.3f (%d/%d scored)",
                arm, summary["mean_score"], summary["n_scored"], summary["n_questions"])
    return summary


def run_phase6_2_gate(path: Optional[Path] = None) -> Dict:
    """Side-by-side legacy-vs-tier3 gate on the macro question set.

    Snapshots the tier3 result (legacy baseline alongside, per-question
    rows carry legacy_score) to ``sage_eval_latest`` with the verdict.
    """
    legacy = run_sage_eval(path, quiet=True, arm="legacy")
    tier3 = run_sage_eval(path, quiet=True, arm="tier3")
    if not legacy.get("ran") or not tier3.get("ran"):
        return {"ran": False, "legacy": legacy, "tier3": tier3}

    faithfulness_ok = tier3["mean_score"] >= legacy["mean_score"] - FAITHFULNESS_EPS
    efficiency_ok = (
        legacy["mean_chars_per_month"] > 0
        and tier3["mean_chars_per_month"]
        <= legacy["mean_chars_per_month"] * (1.0 - EFFICIENCY_MIN_REDUCTION)
    )
    coverage_ok = tier3["month_coverage"] >= COVERAGE_MIN
    n_ok = (
        legacy["n_scored"] >= MIN_SCORED_PER_ARM
        and tier3["n_scored"] >= MIN_SCORED_PER_ARM
    )
    gate_passed = faithfulness_ok and efficiency_ok and coverage_ok and n_ok

    l_by_id = {r["id"]: r for r in legacy["per_question"]}
    merged_rows: List[Dict] = [
        {**r, "legacy_score": l_by_id.get(r["id"], {}).get("score"),
         "legacy_context_chars": l_by_id.get(r["id"], {}).get("context_chars", 0)}
        for r in tier3["per_question"]
    ]

    # Durable record FIRST — a console-encoding crash in the print table
    # below must never lose a completed gate run (cp1252 lesson, 6.1/6.2).
    try:
        from core.supabase_client import get_supabase_manager
        manager = get_supabase_manager()
        if manager and manager.is_available():
            manager.upsert_app_setting("sage_eval_latest", {
                "set_id": SAGE_SET_ID,
                "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "arm": "tier3",
                "mean_score": tier3["mean_score"],
                "n_scored": tier3["n_scored"],
                "n_questions": tier3["n_questions"],
                "mean_chars_per_month": tier3["mean_chars_per_month"],
                "month_coverage": tier3["month_coverage"],
                "latency_mean_ms": tier3["latency_mean_ms"],
                "baseline_legacy_score": legacy["mean_score"],
                "baseline_legacy_chars_per_month": legacy["mean_chars_per_month"],
                "gate_passed": gate_passed,
                "faithfulness_ok": faithfulness_ok,
                "efficiency_ok": efficiency_ok,
                "coverage_ok": coverage_ok,
                "per_question": merged_rows,
            })
    except Exception as e:
        logger.debug("Failed to snapshot Phase 6.2 gate: %s", e)

    reduction = (
        1.0 - tier3["mean_chars_per_month"] / legacy["mean_chars_per_month"]
        if legacy["mean_chars_per_month"] > 0 else 0.0
    )
    print(f"\nPhase 6.2 gate - {tier3['n_questions']} macro questions, faithfulness side-by-side")
    print(f"{'id':<5} {'legacy':>7} {'tier3':>7} {'delta':>6}  {'chars(L->T)':>14}  question")
    for r in merged_rows:
        ls, ts = r.get("legacy_score"), r.get("score")
        delta = f"{ts - ls:+.2f}" if ls is not None and ts is not None else "  -"
        lss = f"{ls:.2f}" if ls is not None else "skip"
        tss = f"{ts:.2f}" if ts is not None else "skip"
        chars = f"{r.get('legacy_context_chars', 0)}->{r.get('context_chars', 0)}"
        print(f"{r['id']:<5} {lss:>7} {tss:>7} {delta:>6}  {chars:>14}  {r['question'][:50]}")
    print(f"\nMean faithfulness: legacy {legacy['mean_score']:.3f} -> tier3 {tier3['mean_score']:.3f} "
          f"({'PASS' if faithfulness_ok else 'REGRESSION'}, eps {FAITHFULNESS_EPS})")
    print(f"Chars/covered-month: legacy {legacy['mean_chars_per_month']} -> tier3 {tier3['mean_chars_per_month']} "
          f"({reduction:.0%} reduction, need >= {EFFICIENCY_MIN_REDUCTION:.0%}: "
          f"{'PASS' if efficiency_ok else 'FAIL'})")
    print(f"Month coverage (tier3): {tier3['month_coverage']:.2f} "
          f"(need >= {COVERAGE_MIN}: {'PASS' if coverage_ok else 'FAIL'})")
    print(f"Scored: legacy {legacy['n_scored']}, tier3 {tier3['n_scored']} "
          f"(need >= {MIN_SCORED_PER_ARM} each: {'PASS' if n_ok else 'FAIL'})")
    print(f"GATE: {'PASSED' if gate_passed else 'NOT PASSED'}")

    return {
        "ran": True,
        "gate_passed": gate_passed,
        "faithfulness_ok": faithfulness_ok,
        "efficiency_ok": efficiency_ok,
        "coverage_ok": coverage_ok,
        "n_ok": n_ok,
        "legacy": legacy,
        "tier3": tier3,
    }


# Phase 7 gate: the deep-research pipeline must not regress the fast path's
# faithfulness, must keep month coverage, and must stay inside a latency
# budget (deep mode trades ~2-3x latency for sub-query retrieval + checking).
DEEP_MEAN_LATENCY_BUDGET_MS = 60_000.0


def run_phase7_gate(path: Optional[Path] = None) -> Dict:
    """Side-by-side fast(tier3)-vs-deep gate on the macro question set.

    Gate: deep mean faithfulness >= fast - FAITHFULNESS_EPS; deep month
    coverage >= COVERAGE_MIN; deep mean latency <= DEEP_MEAN_LATENCY_BUDGET_MS;
    both arms score >= MIN_SCORED_PER_ARM.  Snapshots to sage_eval_latest.
    """
    fast = run_sage_eval(path, quiet=True, arm="tier3")
    deep = run_sage_eval(path, quiet=True, arm="deep")
    if not fast.get("ran") or not deep.get("ran"):
        return {"ran": False, "fast": fast, "deep": deep}

    faithfulness_ok = deep["mean_score"] >= fast["mean_score"] - FAITHFULNESS_EPS
    coverage_ok = deep["month_coverage"] >= COVERAGE_MIN
    latency_ok = deep["latency_mean_ms"] <= DEEP_MEAN_LATENCY_BUDGET_MS
    n_ok = (
        fast["n_scored"] >= MIN_SCORED_PER_ARM
        and deep["n_scored"] >= MIN_SCORED_PER_ARM
    )
    gate_passed = faithfulness_ok and coverage_ok and latency_ok and n_ok

    f_by_id = {r["id"]: r for r in fast["per_question"]}
    merged_rows: List[Dict] = [
        {**r, "fast_score": f_by_id.get(r["id"], {}).get("score")}
        for r in deep["per_question"]
    ]

    # Durable record first (cp1252 lesson).
    try:
        from core.supabase_client import get_supabase_manager
        manager = get_supabase_manager()
        if manager and manager.is_available():
            manager.upsert_app_setting("sage_eval_latest", {
                "set_id": SAGE_SET_ID,
                "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "arm": "deep",
                "mean_score": deep["mean_score"],
                "n_scored": deep["n_scored"],
                "n_questions": deep["n_questions"],
                "mean_chars_per_month": deep["mean_chars_per_month"],
                "month_coverage": deep["month_coverage"],
                "latency_mean_ms": deep["latency_mean_ms"],
                "baseline_fast_score": fast["mean_score"],
                "gate_passed": gate_passed,
                "faithfulness_ok": faithfulness_ok,
                "coverage_ok": coverage_ok,
                "latency_ok": latency_ok,
                "per_question": merged_rows,
            })
    except Exception as e:
        logger.debug("Failed to snapshot Phase 7 gate: %s", e)

    print(f"\nPhase 7 gate - {deep['n_questions']} macro questions, fast vs deep")
    print(f"{'id':<5} {'fast':>7} {'deep':>7} {'delta':>6}  {'latency(deep)':>14}  question")
    for r in merged_rows:
        fs, ds = r.get("fast_score"), r.get("score")
        delta = f"{ds - fs:+.2f}" if fs is not None and ds is not None else "  -"
        fss = f"{fs:.2f}" if fs is not None else "skip"
        dss = f"{ds:.2f}" if ds is not None else "skip"
        lat = f"{r.get('latency_ms', 0) / 1000:.1f}s"
        print(f"{r['id']:<5} {fss:>7} {dss:>7} {delta:>6}  {lat:>14}  {r['question'][:50]}")
    print(f"\nMean faithfulness: fast {fast['mean_score']:.3f} -> deep {deep['mean_score']:.3f} "
          f"({'PASS' if faithfulness_ok else 'REGRESSION'}, eps {FAITHFULNESS_EPS})")
    print(f"Month coverage (deep): {deep['month_coverage']:.2f} "
          f"(need >= {COVERAGE_MIN}: {'PASS' if coverage_ok else 'FAIL'})")
    print(f"Deep mean latency: {deep['latency_mean_ms'] / 1000:.1f}s "
          f"(budget {DEEP_MEAN_LATENCY_BUDGET_MS / 1000:.0f}s: {'PASS' if latency_ok else 'OVER'})")
    print(f"Scored: fast {fast['n_scored']}, deep {deep['n_scored']} "
          f"(need >= {MIN_SCORED_PER_ARM} each: {'PASS' if n_ok else 'FAIL'})")
    print(f"GATE: {'PASSED' if gate_passed else 'NOT PASSED'}")

    return {
        "ran": True,
        "gate_passed": gate_passed,
        "faithfulness_ok": faithfulness_ok,
        "coverage_ok": coverage_ok,
        "latency_ok": latency_ok,
        "n_ok": n_ok,
        "fast": fast,
        "deep": deep,
    }


if __name__ == "__main__":
    import argparse
    from core.logger import setup_logger
    setup_logger("sage_eval")
    parser = argparse.ArgumentParser(description="Sage macro-faithfulness eval / Phase 6.2 + 7 gates")
    parser.add_argument("--arm", choices=["legacy", "tier3", "deep"], default="tier3")
    parser.add_argument("--gate", action="store_true", help="run the Phase 6.2 side-by-side gate")
    parser.add_argument("--gate7", action="store_true", help="run the Phase 7 fast-vs-deep gate")
    args = parser.parse_args()
    if args.gate7:
        run_phase7_gate()
    elif args.gate:
        run_phase6_2_gate()
    else:
        run_sage_eval(arm=args.arm)