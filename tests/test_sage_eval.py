"""Tests for core/sage_eval.py (Phase 6.2 macro-faithfulness gate).

All offline: supabase/gateway/judge are mocked; the conftest autouse stubs
keep everything else offline too.
"""

import json
from unittest.mock import MagicMock, patch

from core.sage_eval import (
    COVERAGE_MIN,
    FAITHFULNESS_EPS,
    JUDGE_CONTEXT_CHARS,
    _covered_months,
    _month_span,
    load_questions,
    run_phase6_2_gate,
    run_sage_eval,
)


def _two_question_set(tmp_path):
    p = tmp_path / "sage_set.json"
    p.write_text(json.dumps({"questions": [
        {"id": "m01", "question": "Q one?", "expected_months": ["2026-05", "2026-06"]},
        {"id": "m02", "question": "Q two?", "expected_months": ["2026-06"]},
    ]}), encoding="utf-8")
    return p


def _ctx(chars=1000, tier3_months=None):
    return {
        "context": "x" * chars,
        "run_count": 10, "date_count": 5,
        "date_range": "2026-05-01 → 2026-06-30",
        "article_count": 0, "articles": [],
        "tier3_block_count": len(tier3_months or ["2026-05"]),
        "tier3_months": tier3_months if tier3_months is not None else ["2026-05"],
    }


def _chat_ok():
    return {"response": "An answer.", "provenance": {"method": "llm", "model": "qwen3.8-flash"}}


def _available_manager():
    m = MagicMock()
    m.is_available.return_value = True
    return m


def _run_arm(path, arm="tier3", ctx=None, chat=None, judge=(0.9, [], None)):
    """Run one arm with everything external mocked; returns (summary, mocks)."""
    manager = _available_manager()
    with patch("core.supabase_client.get_supabase_manager", return_value=manager), \
         patch("core.evaluator._gateway_has_providers", return_value=True), \
         patch("core.evaluator.GatewayJudgeLLM"), \
         patch("core.sage_agent.build_wiki_context", return_value=ctx or _ctx()) as build, \
         patch("core.sage_agent.chat_with_sage", return_value=chat or _chat_ok()), \
         patch("core.evaluator.judge_sage_answer", return_value=judge) as judge_mock:
        summary = run_sage_eval(path, quiet=True, arm=arm)
    return summary, {"manager": manager, "build": build, "judge": judge_mock}


# ---------------------------------------------------------------------------
# Loader / helpers
# ---------------------------------------------------------------------------

def test_load_questions_from_git_tracked_set():
    questions = load_questions()
    assert len(questions) == 8
    assert all(q.get("expected_months") for q in questions)
    assert questions[0]["id"] == "m01"


def test_month_span_and_covered_months():
    assert _month_span("2026-11", "2027-02") == ["2026-11", "2026-12", "2027-01", "2027-02"]
    ctx = {"date_range": "2026-05-15 → 2026-07-02", "tier3_months": ["2026-09"]}
    assert _covered_months(ctx) == ["2026-05", "2026-06", "2026-07", "2026-09"]
    assert _covered_months({"date_range": "", "tier3_months": []}) == []


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------

def test_no_questions_degrades(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"questions": []}), encoding="utf-8")
    assert run_sage_eval(p, quiet=True) == {"ran": False, "reason": "no_questions"}


def test_supabase_unavailable_degrades(tmp_path):
    p = _two_question_set(tmp_path)
    m = MagicMock()
    m.is_available.return_value = False
    with patch("core.supabase_client.get_supabase_manager", return_value=m):
        assert run_sage_eval(p, quiet=True) == {"ran": False, "reason": "supabase_unavailable"}


def test_no_gateway_providers_degrades(tmp_path):
    p = _two_question_set(tmp_path)
    with patch("core.supabase_client.get_supabase_manager", return_value=_available_manager()), \
         patch("core.evaluator._gateway_has_providers", return_value=False):
        assert run_sage_eval(p, quiet=True) == {"ran": False, "reason": "no_gateway_providers"}


# ---------------------------------------------------------------------------
# Rows / means / exclusions
# ---------------------------------------------------------------------------

def test_row_fields_and_means(tmp_path):
    p = _two_question_set(tmp_path)
    summary, _ = _run_arm(p)
    assert summary["ran"] and summary["n_scored"] == 2
    assert summary["mean_score"] == 0.9
    row = summary["per_question"][0]
    assert row["context_chars"] == 1000
    assert row["covered_months"] == ["2026-05", "2026-06"]
    assert row["expected_month_hits"] == 2
    assert row["tier3_block_count"] == 1
    # 1000 chars / 2 covered months = 500.
    assert summary["mean_chars_per_month"] == 500.0
    assert summary["month_coverage"] == 1.0


def test_chat_failure_excluded_not_zero(tmp_path):
    p = _two_question_set(tmp_path)
    chat = {"response": "notice", "provenance": {"method": "failed"}}
    summary, _ = _run_arm(p, chat=chat)
    assert summary["n_scored"] == 0
    assert all(r["failure"] == "chat_failed" for r in summary["per_question"])
    assert all(r["score"] is None for r in summary["per_question"])


def test_judge_failure_excluded_not_zero(tmp_path):
    p = _two_question_set(tmp_path)
    summary, _ = _run_arm(p, judge=(None, None, "infra_error"))
    assert summary["n_scored"] == 0
    assert all(r["failure"] == "infra_error" for r in summary["per_question"])


def test_legacy_arm_applies_kill_switch_override(tmp_path):
    p = _two_question_set(tmp_path)
    seen = []

    def _capture(*args, **kwargs):
        from config.settings import get_sage_settings
        seen.append(get_sage_settings()["sage_tier3_enabled"])
        return _ctx()

    manager = _available_manager()
    with patch("core.supabase_client.get_supabase_manager", return_value=manager), \
         patch("core.evaluator._gateway_has_providers", return_value=True), \
         patch("core.evaluator.GatewayJudgeLLM"), \
         patch("core.sage_agent.build_wiki_context", side_effect=_capture), \
         patch("core.sage_agent.chat_with_sage", return_value=_chat_ok()), \
         patch("core.evaluator.judge_sage_answer", return_value=(0.9, [], None)):
        run_sage_eval(p, quiet=True, arm="legacy")
        run_sage_eval(p, quiet=True, arm="tier3")
    assert seen[:2] == [False, False]
    assert seen[2:] == [True, True]


def test_judge_context_window_raised_symmetrically(tmp_path):
    p = _two_question_set(tmp_path)
    seen = []

    def _capture(*args, **kwargs):
        import core.evaluator as ev
        seen.append(ev.EVAL_SAGE_CONTEXT_CHARS)
        return (0.9, [], None)

    manager = _available_manager()
    with patch("core.supabase_client.get_supabase_manager", return_value=manager), \
         patch("core.evaluator._gateway_has_providers", return_value=True), \
         patch("core.evaluator.GatewayJudgeLLM"), \
         patch("core.sage_agent.build_wiki_context", return_value=_ctx()), \
         patch("core.sage_agent.chat_with_sage", return_value=_chat_ok()), \
         patch("core.evaluator.judge_sage_answer", side_effect=_capture):
        run_sage_eval(p, quiet=True, arm="legacy")
        run_sage_eval(p, quiet=True, arm="tier3")
    assert seen == [JUDGE_CONTEXT_CHARS] * 4


# ---------------------------------------------------------------------------
# Gate verdict matrix
# ---------------------------------------------------------------------------

def _arm_summary(arm, score=0.9, cpm=10000.0, coverage=1.0, n=8):
    rows = [
        {"id": f"m{i:02d}", "question": f"Q{i}", "score": score,
         "context_chars": int(cpm * 2), "covered_months": ["2026-05", "2026-06"],
         "legacy_score": score}
        for i in range(1, n + 1)
    ]
    return {
        "ran": True, "arm": arm, "n_questions": n, "n_scored": n,
        "mean_score": score, "mean_chars_per_month": cpm,
        "month_coverage": coverage, "latency_mean_ms": 1000.0,
        "per_question": rows,
    }


def _run_gate(legacy, tier3):
    manager = _available_manager()
    with patch("core.sage_eval.run_sage_eval", side_effect=[legacy, tier3]), \
         patch("core.supabase_client.get_supabase_manager", return_value=manager):
        verdict = run_phase6_2_gate()
    return verdict, manager


def test_gate_passes_on_no_regression_and_efficiency():
    legacy = _arm_summary("legacy", score=0.9, cpm=10000.0)
    tier3 = _arm_summary("tier3", score=0.9 + FAITHFULNESS_EPS / 2, cpm=3000.0)
    verdict, manager = _run_gate(legacy, tier3)
    assert verdict["gate_passed"] is True
    assert verdict["faithfulness_ok"] and verdict["efficiency_ok"] and verdict["coverage_ok"]
    key, value = manager.upsert_app_setting.call_args.args
    assert key == "sage_eval_latest"
    assert value["gate_passed"] is True
    assert value["baseline_legacy_score"] == 0.9
    assert value["per_question"][0]["legacy_score"] == 0.9


def test_gate_fails_on_faithfulness_regression():
    legacy = _arm_summary("legacy", score=0.9, cpm=10000.0)
    tier3 = _arm_summary("tier3", score=0.9 - FAITHFULNESS_EPS - 0.01, cpm=3000.0)
    verdict, _ = _run_gate(legacy, tier3)
    assert verdict["gate_passed"] is False
    assert verdict["faithfulness_ok"] is False


def test_gate_fails_on_insufficient_efficiency():
    legacy = _arm_summary("legacy", score=0.9, cpm=10000.0)
    tier3 = _arm_summary("tier3", score=0.9, cpm=9000.0)  # only 10% reduction
    verdict, _ = _run_gate(legacy, tier3)
    assert verdict["gate_passed"] is False
    assert verdict["efficiency_ok"] is False


def test_gate_fails_on_coverage_shortfall():
    legacy = _arm_summary("legacy", score=0.9, cpm=10000.0)
    tier3 = _arm_summary("tier3", score=0.95, cpm=3000.0, coverage=COVERAGE_MIN - 0.1)
    verdict, _ = _run_gate(legacy, tier3)
    assert verdict["gate_passed"] is False
    assert verdict["coverage_ok"] is False


def test_gate_fails_on_too_few_scored():
    legacy = _arm_summary("legacy", n=8)
    tier3 = _arm_summary("tier3", n=5)
    verdict, _ = _run_gate(legacy, tier3)
    assert verdict["gate_passed"] is False
    assert verdict["n_ok"] is False


def test_gate_degrades_when_arm_not_ran():
    legacy = {"ran": False, "reason": "supabase_unavailable"}
    tier3 = _arm_summary("tier3")
    verdict, _ = _run_gate(legacy, tier3)
    assert verdict["ran"] is False


# ---------------------------------------------------------------------------
# Phase 7: deep arm + fast-vs-deep gate
# ---------------------------------------------------------------------------

def test_deep_arm_uses_pipeline_and_judges_rebuilt_context(tmp_path):
    p = _two_question_set(tmp_path)
    rebuilt = "REBUILT " + "x" * 900
    deep_result = {
        "response": "An answer.", "provenance": {"method": "llm"},
        "context": rebuilt,
        "pipeline": {"mode": "deep", "sub_queries": ["a", "b"],
                     "revised": True, "factcheck_ok": True},
    }
    manager = _available_manager()
    with patch("core.supabase_client.get_supabase_manager", return_value=manager), \
         patch("core.evaluator._gateway_has_providers", return_value=True), \
         patch("core.evaluator.GatewayJudgeLLM"), \
         patch("core.sage_agent.build_wiki_context", return_value=_ctx()), \
         patch("core.sage_pipeline.chat_with_sage_deep", return_value=deep_result) as deep, \
         patch("core.evaluator.judge_sage_answer", return_value=(0.9, [], None)) as judge:
        summary = run_sage_eval(p, quiet=True, arm="deep")
    assert deep.call_count == 2
    # The judge sees the pipeline's rebuilt context, not the fast-path one.
    assert judge.call_args.args[3] == rebuilt
    row = summary["per_question"][0]
    assert row["context_chars"] == len(rebuilt)
    assert row["sub_queries"] == 2
    assert row["revised"] is True
    assert row["factcheck_ok"] is True


def _run_gate7(fast, deep):
    manager = _available_manager()
    with patch("core.sage_eval.run_sage_eval", side_effect=[fast, deep]), \
         patch("core.supabase_client.get_supabase_manager", return_value=manager):
        from core.sage_eval import run_phase7_gate
        verdict = run_phase7_gate()
    return verdict, manager


def test_gate7_passes_on_no_regression_within_latency():
    fast = _arm_summary("tier3", score=0.9)
    deep = _arm_summary("deep", score=0.95)
    verdict, manager = _run_gate7(fast, deep)
    assert verdict["gate_passed"] is True
    key, value = manager.upsert_app_setting.call_args.args
    assert key == "sage_eval_latest"
    assert value["arm"] == "deep"
    assert value["baseline_fast_score"] == 0.9
    assert value["per_question"][0]["fast_score"] == 0.9


def test_gate7_fails_on_latency_over_budget():
    from core.sage_eval import DEEP_MEAN_LATENCY_BUDGET_MS
    fast = _arm_summary("tier3", score=0.9)
    deep = _arm_summary("deep", score=0.95)
    deep["latency_mean_ms"] = DEEP_MEAN_LATENCY_BUDGET_MS + 1000
    verdict, _ = _run_gate7(fast, deep)
    assert verdict["gate_passed"] is False
    assert verdict["latency_ok"] is False


def test_gate7_fails_on_faithfulness_regression():
    fast = _arm_summary("tier3", score=0.95)
    deep = _arm_summary("deep", score=0.80)
    verdict, _ = _run_gate7(fast, deep)
    assert verdict["gate_passed"] is False
    assert verdict["faithfulness_ok"] is False


# ---------------------------------------------------------------------------
# Phase 8.4: regression gate vs the accumulated live baseline
# ---------------------------------------------------------------------------

def _run_gate8(current, baseline=(0.9, 20), prev=None):
    from core.sage_eval import run_phase8_gate
    manager = _available_manager()
    manager.get_app_setting.return_value = (
        {"arm": "gate8", "mean_score": prev} if prev is not None else {}
    )
    with patch("core.supabase_client.get_supabase_manager", return_value=manager), \
         patch("core.sage_guard.rolling_sage_faithfulness", return_value=baseline), \
         patch("core.sage_eval.run_sage_eval", return_value=current):
        verdict = run_phase8_gate()
    return verdict, manager


def test_gate8_needs_a_baseline():
    current = _arm_summary("tier3", score=0.9)
    verdict, _ = _run_gate8(current, baseline=(0.9, 5))
    assert verdict["ran"] is False
    assert verdict["reason"] == "baseline_too_small"


def test_gate8_passes_and_snapshots():
    current = _arm_summary("tier3", score=0.9)
    verdict, manager = _run_gate8(current, baseline=(0.88, 25))
    assert verdict["gate_passed"] is True
    key, value = manager.upsert_app_setting.call_args.args
    assert key == "sage_eval_latest"
    assert value["arm"] == "gate8"
    assert value["baseline_live_mean"] == 0.88
    assert value["gate_passed"] is True


def test_gate8_fails_on_regression_vs_live_baseline():
    current = _arm_summary("tier3", score=0.80)
    verdict, _ = _run_gate8(current, baseline=(0.9, 25))
    assert verdict["gate_passed"] is False
    assert verdict["baseline_ok"] is False


def test_gate8_fails_on_regression_vs_previous_gate():
    current = _arm_summary("tier3", score=0.90)
    verdict, _ = _run_gate8(current, baseline=(0.85, 25), prev=0.95)
    assert verdict["gate_passed"] is False
    assert verdict["prev_ok"] is False
    assert verdict["baseline_ok"] is True